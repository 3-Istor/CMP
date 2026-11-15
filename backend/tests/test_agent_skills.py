from app.services.agents import skills


def test_front_matter_with_unquoted_colon_is_ignored_instead_of_raising():
    text = "---\nname: cnp-run-app\ndescription: Operate an app that runs: restart it\n---\nBody"

    meta = skills._front_matter(text)

    assert meta == {}


def test_front_matter_reads_name_and_description():
    text = '---\nname: cnp-platform\ndescription: "Read first: rules"\n---\nBody'

    meta = skills._front_matter(text)

    assert meta == {"name": "cnp-platform", "description": "Read first: rules"}
