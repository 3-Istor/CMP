from app.services.clusters.progress import is_resource_done, plan_total


def test_plan_total_add():
    assert plan_total("Plan: 42 to add, 0 to change, 0 to destroy.") == 42


def test_plan_total_destroy():
    assert plan_total("Plan: 0 to add, 0 to change, 37 to destroy.") == 37


def test_plan_total_other_line():
    assert plan_total("aws_vpc.this: Creating...") is None


def test_resource_done():
    assert is_resource_done("aws_vpc.this: Creation complete after 2s [id=vpc-1]", "up")


def test_resource_done_wrong_op():
    assert not is_resource_done("x: Creation complete after 2s", "down")
