"""Shared pytest fixtures and test configurations."""

import pytest

from src.tools.mock_env import MockEnvironment
from src.tools.scenario_config import ScenarioConfig


@pytest.fixture
def default_config() -> ScenarioConfig:
    """Fixture providing a deterministic default scenario configuration.

    Returns:
        ScenarioConfig: Default config with seed 42.
    """
    return ScenarioConfig(seed=42)


@pytest.fixture
def mock_env(default_config: ScenarioConfig) -> MockEnvironment:
    """Fixture providing an initialized MockEnvironment.

    Args:
        default_config: Injected default config.

    Returns:
        MockEnvironment: Seeded mock environment instance.
    """
    return MockEnvironment(default_config)
