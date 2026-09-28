from jobscout.normalization import canonicalize_url, countries_from_locations, html_to_text, infer_seniority


def test_canonical_url_preserves_job_identifier_and_drops_tracking() -> None:
    value = "HTTPS://Example.com/jobs/?gh_jid=123&utm_source=mail#section"

    assert canonicalize_url(value) == "https://example.com/jobs?gh_jid=123"


def test_html_to_text_ignores_script_and_style_content() -> None:
    value = "<style>hidden style</style><p>Visible text</p><script>hidden script</script>"

    assert html_to_text(value) == "Visible text"


def test_html_to_text_decodes_escaped_greenhouse_markup() -> None:
    value = "&lt;p&gt;Fluency in &lt;strong&gt;Bulgarian&lt;/strong&gt; required.&lt;/p&gt;"

    assert html_to_text(value) == "Fluency in Bulgarian required."


def test_country_detection_handles_canadian_cities_and_non_target_countries() -> None:
    assert countries_from_locations(["Ottawa or Toronto"]) == ["CA"]
    assert countries_from_locations(["Singapore"]) == ["SG"]
    assert countries_from_locations(["Brussels"]) == ["BE"]
    assert countries_from_locations(["Shanghai"]) == ["CN"]


def test_project_manager_is_not_automatically_a_lead_role() -> None:
    assert infer_seniority("Project Manager for European Standards") == "unknown"
    assert infer_seniority("Team Lead, Climate Policy") == "lead"


def test_traineeships_are_classified_as_intern_roles() -> None:
    for title in (
        "Policy Trainee",
        "Sustainability Traineeship",
        "Praktikum ESG Reporting",
    ):
        assert infer_seniority(title) == "intern"
