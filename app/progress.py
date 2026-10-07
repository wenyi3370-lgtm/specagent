"""Case completion snapshots; final statuses still come from the shared judge."""


def progress_snapshot(results, total, *, phase="running", last_case=None, cancellable=False):
    statuses = [result.status for result in results]
    return {
        "available": True, "phase": phase, "final": phase == "completed",
        "cancellable": bool(cancellable and phase == "running"),
        "completed": len(results), "total": total,
        "passed": statuses.count("PASS"),
        "failed": statuses.count("FAIL") + statuses.count("FLAKY"),
        "flaky": statuses.count("FLAKY"), "errors": statuses.count("ERROR"),
        "canceled": statuses.count("CANCELED"), "last_case": last_case,
    }
