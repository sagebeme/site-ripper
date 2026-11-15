from site_ripper.paths import local_path, normalize, relative_link, safe_relpath

HOME = "example.com"


def page(url: str) -> str:
    return local_path(url, is_page=True, home_host=HOME)


def test_pages_always_end_in_html() -> None:
    assert page("https://example.com/") == "example.com/index.html"
    assert page("https://example.com/docs/") == "example.com/docs/index.html"
    assert page("https://example.com/about") == "example.com/about/index.html"
    assert page("https://example.com/a/b.htm") == "example.com/a/b.htm"
    assert page("https://example.com/list.php") == "example.com/list.php.html"


def test_query_string_becomes_part_of_name() -> None:
    assert page("https://example.com/list?page=2") == "example.com/list_page_2/index.html"


def test_other_hosts_go_under_assets() -> None:
    got = local_path("https://cdn.example.net/a.css?v=3", is_page=False, home_host=HOME)
    assert got == "_assets/cdn.example.net/a_v_3.css"


def test_missing_extension_comes_from_content_type() -> None:
    got = local_path(
        "https://example.com/logo", is_page=False, home_host=HOME, content_type="image/png"
    )
    assert got == "example.com/logo.png"


def test_paths_cannot_escape_the_output_folder() -> None:
    assert ".." not in page("https://example.com/a/%2E%2E/%2E%2E/etc/passwd")
    assert safe_relpath("../../etc/passwd") == "etc/passwd"


def test_normalize_drops_fragment_and_lowercases_host() -> None:
    assert normalize("HTTPS://Example.COM/A#top") == "https://example.com/A"
    assert normalize("https://example.com") == "https://example.com/"


def test_relative_link() -> None:
    assert relative_link("example.com/docs/index.html", "example.com/css/a.css") == "../css/a.css"
    assert relative_link("example.com/index.html", "example.com/a.png") == "a.png"
