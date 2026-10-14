using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace JobScout.Windows
{
    // The job handle stays in the supervisor. Closing it also kills uv's Python child.
    public sealed class OwnedProcess : IDisposable
    {
        private const uint CreateSuspended = 0x00000004;
        private const uint StartUseStdHandles = 0x00000100;
        private const uint KillOnJobClose = 0x00002000;
        private const uint DuplicateSameAccess = 0x00000002;
        private const uint WaitObject0 = 0;
        private const uint WaitTimeout = 258;
        private const uint WaitFailed = 0xffffffff;

        private readonly object gate = new object();
        private SafeKernelHandle process;
        private SafeKernelHandle job;
        private bool disposed;

        private OwnedProcess(SafeKernelHandle jobHandle, SafeKernelHandle processHandle)
        {
            job = jobHandle;
            process = processHandle;
        }

        public static OwnedProcess Start(string executable, string[] arguments, string workingDirectory)
        {
            if (String.IsNullOrWhiteSpace(executable))
                throw new ArgumentException("An executable path is required.", "executable");
            if (String.IsNullOrWhiteSpace(workingDirectory))
                throw new ArgumentException("A working directory is required.", "workingDirectory");
            if (executable.IndexOf('\0') >= 0 || workingDirectory.IndexOf('\0') >= 0)
                throw new ArgumentException("Paths cannot contain a null character.");

            executable = Path.GetFullPath(executable);
            workingDirectory = Path.GetFullPath(workingDirectory);
            StringBuilder commandLine = new StringBuilder(QuoteArgument(executable));
            foreach (string argument in arguments ?? new string[0])
                commandLine.Append(' ').Append(QuoteArgument(argument));
            if (commandLine.Length >= 32767)
                throw new ArgumentException("The Windows command line is too long.", "arguments");

            SafeKernelHandle jobHandle = null;
            SafeKernelHandle processHandle = null;
            SafeKernelHandle threadHandle = null;
            SafeKernelHandle input = null;
            SafeKernelHandle output = null;
            SafeKernelHandle error = null;
            try
            {
                // Null security attributes make the job handle noninheritable.
                IntPtr rawJob = CreateJobObjectW(IntPtr.Zero, null);
                if (rawJob == IntPtr.Zero)
                    throw LastError("Could not create the dashboard process job.");
                jobHandle = new SafeKernelHandle(rawJob);
                ExtendedJobLimits limits = new ExtendedJobLimits();
                limits.BasicLimits.LimitFlags = KillOnJobClose;
                if (!SetInformationJobObject(jobHandle, 9, ref limits,
                    (uint)Marshal.SizeOf(typeof(ExtendedJobLimits))))
                    throw LastError("Could not configure dashboard process cleanup.");

                input = DuplicateStandardHandle(-10);
                output = DuplicateStandardHandle(-11);
                error = DuplicateStandardHandle(-12);
                StartupInfo startup = new StartupInfo();
                startup.Size = (uint)Marshal.SizeOf(typeof(StartupInfo));
                startup.Flags = StartUseStdHandles;
                startup.StandardInput = input == null ? IntPtr.Zero : input.DangerousGetHandle();
                startup.StandardOutput = output == null ? IntPtr.Zero : output.DangerousGetHandle();
                startup.StandardError = error == null ? IntPtr.Zero : error.DangerousGetHandle();
                ProcessInfo info;
                if (!CreateProcessW(executable, commandLine, IntPtr.Zero, IntPtr.Zero, true,
                    CreateSuspended, IntPtr.Zero, workingDirectory, ref startup, out info))
                    throw LastError("Could not start the dashboard command.");
                processHandle = new SafeKernelHandle(info.Process);
                threadHandle = new SafeKernelHandle(info.Thread);

                // Assign before resuming: uv cannot spawn an unowned Python process.
                if (!AssignProcessToJobObject(jobHandle, processHandle))
                    throw LastError("Could not own the dashboard process tree.");
                if (ResumeThread(threadHandle) == UInt32.MaxValue)
                    throw LastError("Could not resume the dashboard command.");

                OwnedProcess result = new OwnedProcess(jobHandle, processHandle);
                jobHandle = null;
                processHandle = null;
                return result;
            }
            catch
            {
                // An assignment/resume failure must not leave a suspended child behind.
                if (processHandle != null && !processHandle.IsInvalid)
                {
                    TerminateProcess(processHandle, 1);
                    WaitForSingleObject(processHandle, 5000);
                }
                throw;
            }
            finally
            {
                if (threadHandle != null) threadHandle.Dispose();
                if (input != null) input.Dispose();
                if (output != null) output.Dispose();
                if (error != null) error.Dispose();
                if (jobHandle != null) jobHandle.Dispose();
                if (processHandle != null) processHandle.Dispose();
            }
        }

        public bool HasExited
        {
            get { return WaitForExit(0); }
        }

        public int ExitCode
        {
            get
            {
                lock (gate)
                {
                    ThrowIfDisposed();
                    if (!HasExited)
                        throw new InvalidOperationException("The dashboard command is still running.");
                    uint code;
                    if (!GetExitCodeProcess(process, out code))
                        throw LastError("Could not read the dashboard exit code.");
                    return unchecked((int)code);
                }
            }
        }

        public bool WaitForExit(int milliseconds)
        {
            if (milliseconds < -1)
                throw new ArgumentOutOfRangeException("milliseconds");
            SafeKernelHandle waitingOn;
            bool retained = false;
            lock (gate)
            {
                ThrowIfDisposed();
                waitingOn = process;
                waitingOn.DangerousAddRef(ref retained);
            }
            try
            {
                // Retain the handle without locking out Stop during an infinite wait.
                uint result = WaitForSingleObject(waitingOn.DangerousGetHandle(),
                    unchecked((uint)milliseconds));
                if (result == WaitObject0) return true;
                if (result == WaitTimeout) return false;
                if (result == WaitFailed)
                    throw LastError("Could not wait for the dashboard command.");
                throw new InvalidOperationException("Unexpected dashboard process wait result.");
            }
            finally
            {
                if (retained) waitingOn.DangerousRelease();
            }
        }

        public void Stop()
        {
            lock (gate)
            {
                if (disposed || job == null) return;
                SafeKernelHandle stoppingJob = job;
                job = null;
                try
                {
                    if (!TerminateJobObject(stoppingJob, 1))
                        throw LastError("Could not stop the dashboard process tree.");
                    Stopwatch deadline = Stopwatch.StartNew();
                    while (true)
                    {
                        BasicJobAccounting accounting;
                        if (!QueryInformationJobObject(stoppingJob, 1, out accounting,
                            (uint)Marshal.SizeOf(typeof(BasicJobAccounting)), IntPtr.Zero))
                            throw LastError("Could not confirm dashboard process cleanup.");
                        if (accounting.ActiveProcesses == 0) break;
                        if (deadline.ElapsedMilliseconds >= 5000)
                            throw new TimeoutException("The dashboard process tree did not stop within five seconds.");
                        Thread.Sleep(25);
                    }
                }
                finally
                {
                    // Retain kill-on-close cleanup even if termination/query/wait fails.
                    stoppingJob.Dispose();
                }
            }
        }

        public void Dispose()
        {
            lock (gate)
            {
                if (disposed) return;
                try
                {
                    Stop();
                }
                finally
                {
                    process.Dispose();
                    disposed = true;
                }
            }
        }

        private void ThrowIfDisposed()
        {
            if (disposed) throw new ObjectDisposedException("OwnedProcess");
        }

        // Microsoft CRT quoting: double backslashes before quotes and at the end.
        private static string QuoteArgument(string argument)
        {
            if (argument == null || argument.IndexOf('\0') >= 0)
                throw new ArgumentException("Arguments cannot be null or contain a null character.");
            StringBuilder quoted = new StringBuilder();
            quoted.Append('"');
            int slashes = 0;
            foreach (char character in argument)
            {
                if (character == '\\')
                {
                    slashes++;
                    continue;
                }
                quoted.Append('\\', character == '"' ? slashes * 2 + 1 : slashes);
                quoted.Append(character);
                slashes = 0;
            }
            quoted.Append('\\', slashes * 2).Append('"');
            return quoted.ToString();
        }

        private static SafeKernelHandle DuplicateStandardHandle(int number)
        {
            IntPtr source = GetStdHandle(number);
            if (source == IntPtr.Zero || source == new IntPtr(-1)) return null;
            IntPtr duplicate;
            IntPtr current = GetCurrentProcess();
            if (!DuplicateHandle(current, source, current, out duplicate, 0, true, DuplicateSameAccess))
                throw LastError("Could not inherit the console handles.");
            return new SafeKernelHandle(duplicate);
        }

        private static Win32Exception LastError(string message)
        {
            return new Win32Exception(Marshal.GetLastWin32Error(), message);
        }

        private sealed class SafeKernelHandle : SafeHandleZeroOrMinusOneIsInvalid
        {
            public SafeKernelHandle(IntPtr value) : base(true) { SetHandle(value); }
            protected override bool ReleaseHandle() { return CloseHandle(handle); }
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct BasicJobLimits
        {
            public long ProcessTimeLimit;
            public long JobTimeLimit;
            public uint LimitFlags;
            public UIntPtr MinimumWorkingSet;
            public UIntPtr MaximumWorkingSet;
            public uint ActiveProcessLimit;
            public UIntPtr Affinity;
            public uint PriorityClass;
            public uint SchedulingClass;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct IoCounters
        {
            public ulong ReadOperations;
            public ulong WriteOperations;
            public ulong OtherOperations;
            public ulong ReadBytes;
            public ulong WriteBytes;
            public ulong OtherBytes;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct BasicJobAccounting
        {
            public long TotalUserTime;
            public long TotalKernelTime;
            public long PeriodUserTime;
            public long PeriodKernelTime;
            public uint TotalPageFaults;
            public uint TotalProcesses;
            public uint ActiveProcesses;
            public uint TotalTerminatedProcesses;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ExtendedJobLimits
        {
            public BasicJobLimits BasicLimits;
            public IoCounters Io;
            public UIntPtr ProcessMemoryLimit;
            public UIntPtr JobMemoryLimit;
            public UIntPtr PeakProcessMemory;
            public UIntPtr PeakJobMemory;
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct StartupInfo
        {
            public uint Size;
            public string Reserved;
            public string Desktop;
            public string Title;
            public uint X;
            public uint Y;
            public uint XSize;
            public uint YSize;
            public uint XCharacters;
            public uint YCharacters;
            public uint FillAttribute;
            public uint Flags;
            public ushort ShowWindow;
            public ushort ReservedSize;
            public IntPtr ReservedData;
            public IntPtr StandardInput;
            public IntPtr StandardOutput;
            public IntPtr StandardError;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct ProcessInfo
        {
            public IntPtr Process;
            public IntPtr Thread;
            public uint ProcessId;
            public uint ThreadId;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        private static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SetInformationJobObject(SafeKernelHandle job, int infoClass,
            ref ExtendedJobLimits information, uint length);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CreateProcessW(string application, StringBuilder commandLine,
            IntPtr processAttributes, IntPtr threadAttributes, [MarshalAs(UnmanagedType.Bool)] bool inheritHandles,
            uint flags, IntPtr environment, string directory, ref StartupInfo startup, out ProcessInfo info);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool AssignProcessToJobObject(SafeKernelHandle job, SafeKernelHandle process);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool TerminateJobObject(SafeKernelHandle job, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool QueryInformationJobObject(SafeKernelHandle job, int infoClass,
            out BasicJobAccounting information, uint length, IntPtr returnLength);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint ResumeThread(SafeKernelHandle thread);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool TerminateProcess(SafeKernelHandle process, uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(SafeKernelHandle handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetExitCodeProcess(SafeKernelHandle process, out uint exitCode);
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern IntPtr GetStdHandle(int number);
        [DllImport("kernel32.dll")]
        private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr source, IntPtr targetProcess,
            out IntPtr target, uint access, [MarshalAs(UnmanagedType.Bool)] bool inherit, uint options);
        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);
    }
}
