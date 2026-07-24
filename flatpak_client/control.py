"""Testable tray state and daemon control logic without desktop imports."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import threading
from typing import Protocol

from .client import (
    ApiResponse,
    AuthenticationError,
    ConnectionFailure,
    DaemonTokenMissingError,
    LocalApiClient,
    LocalApiClientError,
)


class TrayStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    TRANSITIONING = "transitioning"
    ERROR = "error"


@dataclass(frozen=True)
class TrayState:
    """Bounded, token-free state consumed by any tray backend."""

    status: TrayStatus = TrayStatus.ERROR
    status_label: str = "Error"
    phase: str = "unknown"
    running: bool = False
    daemon_available: bool = False
    authenticated: bool = False
    busy_action: str | None = None
    share_internet: bool | None = None
    privacy_mode: bool = True
    hotspot_autostart: bool | None = None
    message: str = "Authentication is required."
    detail_code: str = "token_missing"


@dataclass(frozen=True)
class ActionOutcome:
    """Fixed, secret-free result for one tray action."""

    accepted: bool
    succeeded: bool
    code: str
    message: str
    state: TrayState


class TokenProvider(Protocol):
    def token_for_operation(self) -> str | None:
        """Return an explicitly entered or previously saved token."""


class TrayControlController:
    """Serialize tray actions and project daemon responses into safe state."""

    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        client_factory=LocalApiClient,
    ):
        self._token_provider = token_provider
        self._client_factory = client_factory
        self._state = TrayState()
        self._state_lock = threading.Lock()
        self._operation_lock = threading.Lock()

    def __repr__(self) -> str:
        return (
            "TrayControlController("
            f"status={self.state.status.value!r}, "
            f"busy={self.state.busy_action is not None!r})"
        )

    @property
    def state(self) -> TrayState:
        with self._state_lock:
            return self._state

    def _set_state(self, state: TrayState) -> TrayState:
        with self._state_lock:
            self._state = state
            return state

    def _error_state(
        self,
        *,
        message: str,
        detail_code: str,
        daemon_available: bool,
        authenticated: bool = False,
    ) -> TrayState:
        current = self.state
        return TrayState(
            status=TrayStatus.ERROR,
            status_label="Error",
            phase="error",
            daemon_available=daemon_available,
            authenticated=authenticated,
            privacy_mode=current.privacy_mode,
            message=message,
            detail_code=detail_code,
        )

    def _client(self):
        token = self._token_provider.token_for_operation()
        if not token:
            return None
        try:
            return self._client_factory(token=token)
        except Exception:
            return None

    def _state_from_responses(
        self,
        status_response: ApiResponse,
        config_response: ApiResponse,
    ) -> TrayState:
        status_data = status_response.data
        config_data = config_response.data
        phase_value = status_data.get("phase")
        phase = phase_value if isinstance(phase_value, str) else "unknown"
        running = status_data.get("running") is True

        if phase in {"starting", "stopping"}:
            status = TrayStatus.TRANSITIONING
            label = "Transitioning"
        elif phase == "error":
            status = TrayStatus.ERROR
            label = "Error"
        elif running or phase == "running":
            status = TrayStatus.RUNNING
            label = "Running"
            running = True
        elif phase == "stopped" or status_data.get("running") is False:
            status = TrayStatus.STOPPED
            label = "Stopped"
            running = False
        else:
            status = TrayStatus.ERROR
            label = "Error"

        share_internet = config_data.get("enable_internet")
        if type(share_internet) is not bool:
            share_internet = None
        hotspot_autostart = config_data.get("autostart")
        if type(hotspot_autostart) is not bool:
            hotspot_autostart = None

        return TrayState(
            status=status,
            status_label=label,
            phase=phase,
            running=running,
            daemon_available=True,
            authenticated=True,
            busy_action=None,
            share_internet=share_internet,
            privacy_mode=self.state.privacy_mode,
            hotspot_autostart=hotspot_autostart,
            message=f"Current status: {label}",
            detail_code=status.value,
        )

    def _fetch_state(self, client) -> TrayState:
        try:
            status_response = client.status(
                include_logs=not self.state.privacy_mode
            )
            config_response = client.config()
            if not isinstance(status_response, ApiResponse) or not isinstance(
                config_response, ApiResponse
            ):
                raise TypeError
            return self._state_from_responses(status_response, config_response)
        except AuthenticationError:
            return self._error_state(
                message="Authentication was rejected.",
                detail_code="authentication_rejected",
                daemon_available=True,
            )
        except DaemonTokenMissingError:
            return self._error_state(
                message="The daemon has no configured API token.",
                detail_code="daemon_token_missing",
                daemon_available=True,
            )
        except ConnectionFailure:
            return self._error_state(
                message="The local daemon is unavailable.",
                detail_code="daemon_unavailable",
                daemon_available=False,
            )
        except (LocalApiClientError, TypeError):
            return self._error_state(
                message="The daemon returned an unsupported response.",
                detail_code="invalid_response",
                daemon_available=True,
            )
        except Exception:
            return self._error_state(
                message="The tray operation failed safely.",
                detail_code="operation_failed",
                daemon_available=False,
            )

    def refresh(self) -> TrayState:
        if not self._operation_lock.acquire(blocking=False):
            return self.state
        try:
            client = self._client()
            if client is None:
                return self._set_state(
                    self._error_state(
                        message="Enter the daemon API token in Authentication.",
                        detail_code="token_missing",
                        daemon_available=False,
                    )
                )
            return self._set_state(self._fetch_state(client))
        finally:
            self._operation_lock.release()

    def _working_state(self, action: str) -> TrayState:
        current = self.state
        return replace(
            current,
            status=TrayStatus.TRANSITIONING,
            status_label="Transitioning",
            phase="working",
            busy_action=action,
            message="Operation in progress.",
            detail_code="operation_in_progress",
        )

    def mark_operation_pending(self, action: str) -> TrayState:
        """Expose transitioning state as soon as the desktop queues an action."""

        if not isinstance(action, str) or not action:
            return self.state
        return self._set_state(self._working_state(action))

    def perform(self, action: str, *, enabled: bool | None = None) -> ActionOutcome:
        if not self._operation_lock.acquire(blocking=False):
            state = self.state
            return ActionOutcome(
                accepted=False,
                succeeded=False,
                code="operation_in_progress",
                message="Another operation is already in progress.",
                state=state,
            )

        self._set_state(self._working_state(action))
        try:
            client = self._client()
            if client is None:
                state = self._set_state(
                    self._error_state(
                        message="Enter the daemon API token in Authentication.",
                        detail_code="token_missing",
                        daemon_available=False,
                    )
                )
                return ActionOutcome(
                    accepted=True,
                    succeeded=False,
                    code="token_missing",
                    message=state.message,
                    state=state,
                )

            calls = {
                "start": client.start_hotspot,
                "stop": client.stop_hotspot,
                "restart": client.restart_service,
                "repair": client.repair_network,
            }
            response = None
            try:
                if action == "share_internet":
                    if type(enabled) is not bool:
                        raise LocalApiClientError(
                            "Share Internet Connection requires a boolean value."
                        )
                    response = client.set_share_internet(enabled)
                elif action == "hotspot_autostart":
                    if type(enabled) is not bool:
                        raise LocalApiClientError(
                            "Start Hotspot Automatically requires a boolean value."
                        )
                    response = client.set_hotspot_autostart(enabled)
                else:
                    call = calls.get(action)
                    if call is None:
                        raise LocalApiClientError("Unsupported tray action.")
                    response = call()
            except AuthenticationError:
                state = self._set_state(
                    self._error_state(
                        message="Authentication was rejected.",
                        detail_code="authentication_rejected",
                        daemon_available=True,
                    )
                )
                return ActionOutcome(
                    accepted=True,
                    succeeded=False,
                    code="authentication_rejected",
                    message=state.message,
                    state=state,
                )
            except DaemonTokenMissingError:
                state = self._set_state(
                    self._error_state(
                        message="The daemon has no configured API token.",
                        detail_code="daemon_token_missing",
                        daemon_available=True,
                    )
                )
                return ActionOutcome(
                    accepted=True,
                    succeeded=False,
                    code="daemon_token_missing",
                    message=state.message,
                    state=state,
                )
            except ConnectionFailure:
                state = self._set_state(
                    self._error_state(
                        message="The local daemon is unavailable.",
                        detail_code="daemon_unavailable",
                        daemon_available=False,
                    )
                )
                return ActionOutcome(
                    accepted=True,
                    succeeded=False,
                    code="daemon_unavailable",
                    message=state.message,
                    state=state,
                )
            except Exception:
                state = self._set_state(
                    self._error_state(
                        message="The requested operation failed.",
                        detail_code="operation_failed",
                        daemon_available=True,
                        authenticated=True,
                    )
                )
                return ActionOutcome(
                    accepted=True,
                    succeeded=False,
                    code="operation_failed",
                    message=state.message,
                    state=state,
                )

            state = self._set_state(self._fetch_state(client))
            messages = {
                "start": ("hotspot_started", "Hotspot started."),
                "stop": ("hotspot_stopped", "Hotspot stopped."),
                "restart": ("service_restarted", "Hotspot service restarted."),
                "repair": ("network_repaired", "Network repair completed."),
                "share_internet": (
                    "share_internet_updated",
                    "Share Internet Connection setting updated.",
                ),
                "hotspot_autostart": (
                    "hotspot_autostart_updated",
                    "Start Hotspot Automatically setting updated.",
                ),
            }
            code, message = messages[action]
            if (
                action == "start"
                and isinstance(response, ApiResponse)
                and response.result_code == "already_running"
            ):
                code, message = (
                    "hotspot_already_running",
                    "The hotspot was already running.",
                )
            elif (
                action == "stop"
                and isinstance(response, ApiResponse)
                and response.result_code == "already_stopped"
            ):
                code, message = (
                    "hotspot_already_stopped",
                    "The hotspot was already stopped.",
                )
            return ActionOutcome(
                accepted=True,
                succeeded=True,
                code=code,
                message=message,
                state=state,
            )
        finally:
            self._operation_lock.release()

    def set_privacy_mode(self, enabled: bool) -> TrayState:
        if type(enabled) is not bool:
            return self.state
        with self._state_lock:
            self._state = replace(self._state, privacy_mode=enabled)
            return self._state
