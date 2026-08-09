from asmr_lrc.reporting import BatchReport


def test_exit_codes_distinguish_success_partial_and_total_failure() -> None:
    assert BatchReport(total=2, succeeded=2).exit_code == 0
    assert BatchReport(total=2, succeeded=1, failed=1).exit_code == 1
    assert BatchReport(total=2, failed=2).exit_code == 2
    assert BatchReport(total=2, skipped=1, failed=1).exit_code == 2
