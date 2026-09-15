"""The exceptions used by Lumo."""

from homeassistant.exceptions import HomeAssistantError


class EntityNotFound(HomeAssistantError):
    """When referenced entity not found."""

    def __init__(self, entity_id: str) -> None:
        """Initialize error."""
        super().__init__(self, f"entity {entity_id} not found")
        self.entity_id = entity_id

    def __str__(self) -> str:
        """Return string representation."""
        return f"Unable to find entity {self.entity_id}"


class EntityNotExposed(HomeAssistantError):
    """When referenced entity not exposed."""

    def __init__(self, entity_id: str) -> None:
        """Initialize error."""
        super().__init__(self, f"entity {entity_id} not exposed")
        self.entity_id = entity_id

    def __str__(self) -> str:
        """Return string representation."""
        return f"entity {self.entity_id} is not exposed"


class CallServiceError(HomeAssistantError):
    """Error during service calling"""

    def __init__(self, domain: str, service: str, data: object) -> None:
        """Initialize error."""
        super().__init__(
            self,
            (
                f"unable to call service {domain}.{service} with data {data}. One of"
                " 'entity_id', 'area_id', or 'device_id' is required"
            ),
        )
        self.domain = domain
        self.service = service
        self.data = data

    def __str__(self) -> str:
        """Return string representation."""
        return (
            f"unable to call service {self.domain}.{self.service} with data"
            f" {self.data}. One of 'entity_id', 'area_id', or 'device_id' is required"
        )


class FunctionNotFound(HomeAssistantError):
    """When referenced function not found."""

    def __init__(self, function: str) -> None:
        """Initialize error."""
        super().__init__(self, f"function '{function}' does not exist")
        self.function = function

    def __str__(self) -> str:
        """Return string representation."""
        return f"function '{self.function}' does not exist"


class NativeNotFound(HomeAssistantError):
    """When native function not found."""

    def __init__(self, name: str) -> None:
        """Initialize error."""
        super().__init__(self, f"native function '{name}' does not exist")
        self.name = name

    def __str__(self) -> str:
        """Return string representation."""
        return f"native function '{self.name}' does not exist"


class ConfigFileNotReadable(HomeAssistantError):
    """When a config file outside the read allowlist is requested."""

    def __init__(self, filename: object, allowed: list[str]) -> None:
        """Initialize error."""
        super().__init__(self, f"reading '{filename}' is not allowed")
        self.filename = filename
        self.allowed = allowed

    def __str__(self) -> str:
        """Return string representation."""
        return f"reading '{self.filename}' is not allowed. Readable files are: {', '.join(self.allowed)}"


class SystemLogUnavailable(HomeAssistantError):
    """When the Logs page's deduplicated error list cannot be read."""

    def __init__(self) -> None:
        """Initialize error."""
        super().__init__(self, "the system_log integration is not loaded")

    def __str__(self) -> str:
        """Return string representation."""
        return (
            "the system_log integration is not loaded, so the deduplicated error list is"
            " unavailable. Call the function again with source 'raw' to read the log file"
            " instead"
        )


class ValueTemplateError(HomeAssistantError):
    """When a function's value_template failed to render."""

    def __init__(self, function_type: str) -> None:
        """Initialize error."""
        super().__init__(self, f"value_template of the {function_type} function failed to render")
        self.function_type = function_type

    def __str__(self) -> str:
        """Return string representation."""
        return (
            f"value_template of the {self.function_type} function failed to render. Home Assistant"
            " logged the underlying Jinja error as 'Error parsing value'"
        )


class FunctionLoadFailed(HomeAssistantError):
    """When function load failed."""

    def __init__(self) -> None:
        """Initialize error."""
        super().__init__(
            self,
            "failed to load functions. Verify functions are valid in a yaml format",
        )

    def __str__(self) -> str:
        """Return string representation."""
        return "failed to load functions. Verify functions are valid in a yaml format"


class ParseArgumentsFailed(HomeAssistantError):
    """When parse arguments failed."""

    def __init__(self, arguments: str) -> None:
        """Initialize error."""
        super().__init__(
            self,
            (f"failed to parse arguments `{arguments}`. Increase maximum token to" " avoid the issue."),
        )
        self.arguments = arguments

    def __str__(self) -> str:
        """Return string representation."""
        return f"failed to parse arguments `{self.arguments}`. Increase maximum token to" " avoid the issue."


class TokenLengthExceededError(HomeAssistantError):
    """When openai return 'length' as 'finish_reason'."""

    def __init__(self, token: int) -> None:
        """Initialize error."""
        super().__init__(
            self,
            (f"token length(`{token}`) exceeded. Increase maximum token to avoid the" " issue."),
        )
        self.token = token

    def __str__(self) -> str:
        """Return string representation."""
        return f"token length(`{self.token}`) exceeded. Increase maximum token to avoid" " the issue."


class InvalidFunction(HomeAssistantError):
    """When function validation failed."""

    def __init__(self, function_name: str) -> None:
        """Initialize error."""
        super().__init__(
            self,
            f"failed to validate function `{function_name}`",
        )
        self.function_name = function_name

    def __str__(self) -> str:
        """Return string representation."""
        return f"failed to validate function `{self.function_name}` ({self.__cause__})"
