import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_targets.badges import SOURCE, badge_args, count_badges, publish
from run_targets.state import ServiceRecord, WorkspaceRecord


def record(**services):
    return WorkspaceRecord(services=dict(services))


def service(pane_id, stop_requested=False):
    return ServiceRecord(tab_id="w1:t1", pane_id=pane_id, stop_requested=stop_requested)


class BadgeClient:
    """A stand-in for the `herdr` module, as far as badges need it."""

    def __init__(self, panes=(), foreground=(), fail=False):
        self.panes = set(panes)
        self.foreground = set(foreground)
        self.fail = fail
        self.calls = []

    def live_pane_ids(self):
        return set(self.panes)

    def process_info(self, pane_id):
        return {"pane_id": pane_id}

    def has_foreground_command(self, info):
        return info["pane_id"] in self.foreground

    def herdr_call(self, args):
        self.calls.append(list(args))
        if self.fail:
            raise RuntimeError("unknown command report-metadata")
        return ""


class CountBadgesTest(unittest.TestCase):
    def test_counts_running_services_as_up(self):
        client = BadgeClient(panes={"p1", "p2"}, foreground={"p1", "p2"})
        self.assertEqual(count_badges(record(api=service("p1"), web=service("p2")), client), (2, 0))

    def test_counts_a_service_that_ended_on_its_own_as_down(self):
        client = BadgeClient(panes={"p1"})
        self.assertEqual(count_badges(record(api=service("p1")), client), (0, 1))

    def test_ignores_a_service_the_user_stopped(self):
        client = BadgeClient(panes={"p1"})
        self.assertEqual(
            count_badges(record(api=service("p1", stop_requested=True)), client), (0, 0)
        )

    def test_ignores_a_service_whose_pane_is_gone(self):
        self.assertEqual(count_badges(record(api=service("p1")), BadgeClient()), (0, 0))


class BadgeArgsTest(unittest.TestCase):
    def test_reports_both_counts_on_the_workspace(self):
        self.assertEqual(
            badge_args("w1", 2, 1),
            [
                "workspace", "report-metadata", "w1", "--source", SOURCE,
                "--token", "svc_up=▶2", "--token", "svc_down=✕1",
            ],
        )

    def test_clears_a_zero_count_so_the_sidebar_hides_it(self):
        args = badge_args("w1", 0, 0)
        self.assertIn("--clear-token", args)
        self.assertEqual(
            args[5:], ["--clear-token", "svc_up", "--clear-token", "svc_down"]
        )


class PublishTest(unittest.TestCase):
    def test_reports_the_counts_and_returns_them(self):
        client = BadgeClient(panes={"p1"}, foreground={"p1"})
        counts = publish("w1", record(api=service("p1")), client)
        self.assertEqual(counts, (1, 0))
        self.assertEqual(client.calls, [badge_args("w1", 1, 0)])

    def test_skips_the_call_when_the_counts_did_not_change(self):
        client = BadgeClient(panes={"p1"}, foreground={"p1"})
        publish("w1", record(api=service("p1")), client, previous=(1, 0))
        self.assertEqual(client.calls, [])

    def test_a_herdr_failure_is_swallowed_and_retried_next_time(self):
        client = BadgeClient(panes={"p1"}, foreground={"p1"}, fail=True)
        self.assertIsNone(publish("w1", record(api=service("p1")), client))

    def test_an_observation_failure_is_swallowed(self):
        client = BadgeClient()

        def boom():
            raise RuntimeError("server restarting")

        client.live_pane_ids = boom
        self.assertIsNone(publish("w1", record(api=service("p1")), client))
        self.assertEqual(client.calls, [])


class HookTest(unittest.TestCase):
    def test_does_nothing_without_a_workspace(self):
        import report_badges

        client = BadgeClient()
        with patch.dict(os.environ, {}, clear=True), \
             patch("report_badges.herdr", client):
            self.assertEqual(report_badges.main(), 0)
        self.assertEqual(client.calls, [])

    def test_reports_the_focused_workspace(self):
        import report_badges

        client = BadgeClient(panes={"p1"})
        state = {"w1": record(api=service("p1"))}
        with patch.dict(os.environ, {"HERDR_WORKSPACE_ID": "w1"}, clear=True), \
             patch("report_badges.herdr", client), \
             patch("report_badges.load_state", return_value=state):
            self.assertEqual(report_badges.main(), 0)
        self.assertEqual(client.calls, [badge_args("w1", 0, 1)])

    def test_clears_the_badges_of_a_workspace_without_services(self):
        import report_badges

        client = BadgeClient()
        with patch.dict(os.environ, {"HERDR_WORKSPACE_ID": "w2"}, clear=True), \
             patch("report_badges.herdr", client), \
             patch("report_badges.load_state", return_value={}):
            report_badges.main()
        self.assertEqual(client.calls, [badge_args("w2", 0, 0)])


if __name__ == "__main__":
    unittest.main()
