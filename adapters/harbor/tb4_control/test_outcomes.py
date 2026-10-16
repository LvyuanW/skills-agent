"""Regression cases for the stopped run's hidden execution failures."""
import unittest

from .outcomes import assess_outcome, classify_result


def scored(reward=0, **extra):
    return {"verifier_result": {"rewards": {"reward": reward}}, **extra}


class OutcomeTests(unittest.TestCase):
    def test_normal_zero_is_scored_and_never_pauses(self):
        for reward in (0, 1, 0.0, 1.0):
            result = assess_outcome(scored(reward))
            self.assertEqual(result["status"], "complete")
            self.assertFalse(result["pause_admission"])

    def test_reported_gateway_and_cleanup_failures_override_verifier_zero(self):
        # Replays the five observed failures at the adapter/controller boundary:
        # missing ps, SSE upstream error, 502+cleanup, 502, Claude 524.
        for name, native in (
            ("SkillsAgentLifecycleError", {"kind": "lifecycle"}),
            ("SkillsAgentProviderError", {"kind": "provider", "provider_code": "upstream_stream_read_error"}),
            ("SkillsAgentProviderError", {"kind": "provider", "http_status": 502, "cleanup_error_count": 1}),
            ("SkillsAgentProviderError", {"kind": "provider", "http_status": 502}),
            ("UnknownApiError", None),
        ):
            with self.subTest(name=name, native=native):
                result = scored(exception_info={"exception_type": name})
                if native:
                    result["agent_result"] = {"metadata": {"native_failure": native}}
                actual = assess_outcome(result)
                self.assertEqual(actual["status"], "needs_review")
                self.assertTrue(actual["pause_admission"])

    def test_native_failure_alone_overrides_even_a_positive_reward(self):
        result = scored(1, agent_result={"metadata": {"native_failure": {"kind": "lifecycle"}}})
        self.assertEqual(classify_result(result), "needs_review")

    def test_unidentified_nonzero_is_not_hidden_by_zero(self):
        for name in ("NonZeroAgentExitCodeError", "AgentProcessError", "RuntimeError"):
            self.assertEqual(classify_result(scored(exception_info={"exception_type": name})), "needs_review")

    def test_known_agent_limits_remain_scorable_but_need_a_verifier(self):
        for name in ("AgentTimeoutError", "OutputTokenExceededError", "ContextWindowExceededError"):
            self.assertEqual(classify_result(scored(exception_info={"exception_type": name})), "complete")
            self.assertEqual(classify_result({"exception_info": {"exception_type": name}}), "needs_review")

    def test_environment_fault_is_explicit_even_if_verifier_wrote_zero(self):
        result = assess_outcome(scored(exception_info={"exception_type": "EnvironmentStartTimeoutError"}))
        self.assertEqual(result["status"], "infra_failure")
        self.assertFalse(result["pause_admission"])
        self.assertEqual(classify_result(scored(exception_info={"exception_type": "ProviderUnavailableError"})), "needs_review")

    def test_native_agent_limit_is_scorable_but_cleanup_still_blocks(self):
        native = {"kind": "agent_limit", "category": "output_limit", "cleanup_error_count": 0}
        result = scored(exception_info={"exception_type": "OutputTokenExceededError"},
                        agent_result={"metadata": {"native_failure": native}})
        self.assertEqual(classify_result(result), "complete")
        native["cleanup_error_count"] = 1
        self.assertEqual(classify_result(result), "needs_review")
        native.update(cleanup_error_count=0, category="invented")
        self.assertEqual(classify_result(result), "needs_review")

    def test_tool_prose_does_not_invent_a_provider_failure(self):
        result = scored(agent_result={"metadata": {"tool_output": "HTTP 502 upstream_error"}})
        self.assertEqual(classify_result(result), "complete")

    def test_secondary_controller_error_is_not_masked(self):
        self.assertEqual(classify_result(scored(
            exception_info={"exception_type": "AgentTimeoutError"},
            controller_error={"type": "RuntimeError", "message": "private details"})), "needs_review")

    def test_replaceable_fault_never_hides_secondary_cleanup_or_health_failure(self):
        for primary in ("EnvironmentStartTimeoutError", "EnvironmentConnectionError"):
            for secondary in ("EnvironmentCleanupError", "EnvironmentHealthError",
                              "ResourceConfigurationError", "RuntimeError"):
                with self.subTest(primary=primary, secondary=secondary):
                    result = assess_outcome(scored(
                        exception_info={"exception_type": primary},
                        controller_error={"type": secondary}))
                    self.assertEqual(result["status"], "needs_review")
                    self.assertTrue(result["pause_admission"])

    def test_malformed_result_is_visible_without_leaking_prose(self):
        for result in (None, {}, scored(True), scored(None), scored(0.5),
                       scored(exception_info={"exception_type": {"invalid": "value"}}),
                       scored(agent_result={"metadata": {"native_failure": {"kind": []}}}),
                       {"controller_error": "secret", "verifier_result": {"rewards": {"reward": 0}}}):
            actual = assess_outcome(result)
            self.assertEqual(actual["status"], "needs_review")
            self.assertNotIn("secret", str(actual))


if __name__ == "__main__":
    unittest.main()
