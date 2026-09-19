"""Run orchestration.

M1 implements a *thin* runtime: a run streams the model's answer to the goal as
persisted events. The agentic layers (planner, executor, reflector) replace this
flow from M2 onward, reusing the same run/event/emit machinery.
"""
