from sales_automation.web import _batch_result


def test_batch_result_exposes_counts_instead_of_tuple() -> None:
    assert _batch_result("enriched", (3, 2)) == {
        "enriched": 3,
        "failed": 2,
        "processed": 5,
    }
