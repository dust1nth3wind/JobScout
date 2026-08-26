from jobscout.normalization import canonicalize_url, countries_from_locations, html_to_text


def test_canonical_url_preserves_job_identifier_and_drops_tracking() -> None:
    value = "HTTPS://Example.com/jobs/?gh_jid=123&utm_source=mail#section"

    assert canonicalize_url(value) == "https://example.com/jobs?gh_jid=123"


def test_html_to_text_ignores_script_and_style_content() -> None:
    value = "<style>hidden style</style><p>Visible text</p><script>hidden script</script>"

    assert html_to_text(value) == "Visible text"


def test_country_detection_handles_canadian_cities_and_non_target_countries() -> None:
    assert countries_from_locations(["Ottawa or Toronto"]) == ["CA"]
    assert countries_from_locations(["Singapore"]) == ["SG"]
