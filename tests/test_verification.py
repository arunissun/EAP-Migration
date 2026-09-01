from eap_migration.verification import compare_expected_payload


def test_verification_ignores_set_order_and_server_owned_fields() -> None:
    report = compare_expected_payload(
        {"partners": [2, 1], "admin2": [4, 3], "title": "same"},
        {"partners": [1, 2], "admin2": [3, 4], "title": "same", "created_by": 99},
    )
    assert report.ok


def test_verification_reports_nested_difference() -> None:
    report = compare_expected_payload(
        {"planned_operations": [{"budget_per_sector": 10}]},
        {"planned_operations": [{"budget_per_sector": 11}]},
    )
    assert not report.ok
    assert report.differences[0]["path"] == "planned_operations.0.budget_per_sector"


def test_verification_normalizes_numeric_full_eap_lead_time() -> None:
    report = compare_expected_payload(
        {"lead_time": "3"},
        {"lead_time": 3},
    )

    assert report.ok


def test_verification_reports_extra_top_level_and_nested_list_items() -> None:
    report = compare_expected_payload(
        {
            "planned_operations": [
                {"indicators": [{"title": "Reach", "target": 10}]}
            ]
        },
        {
            "planned_operations": [
                {
                    "indicators": [
                        {"title": "Reach", "target": 10},
                        {"title": "Extra", "target": 1},
                    ]
                },
                {"indicators": []},
            ]
        },
    )

    assert not report.ok
    assert {
        (difference["path"], difference["expected"])
        for difference in report.differences
    } >= {
        ("planned_operations.0.indicators.1", "<absent>"),
        ("planned_operations.1", "<absent>"),
    }


def test_verification_keeps_order_sensitive_lists_order_sensitive() -> None:
    report = compare_expected_payload(
        {"planned_operations": [{"sector": 101}, {"sector": 102}]},
        {"planned_operations": [{"sector": 102}, {"sector": 101}]},
    )

    assert not report.ok


def test_verification_does_not_collapse_duplicate_list_members() -> None:
    report = compare_expected_payload(
        {"planned_operations": [{"sector": 101}]},
        {"planned_operations": [{"sector": 101}, {"sector": 101}]},
    )

    assert not report.ok
