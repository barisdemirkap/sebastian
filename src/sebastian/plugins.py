"""Plugin registry (ADR 0005): which features are switched on, and what each one contributes.

A plugin is a plain description, not a loaded module: the code stays in the core, the registry only says which commands,
intents, agent tools and digest sections belong to which feature. Config `[plugins]` turns a plugin off; everything is on by
default. A disabled plugin refuses its commands and wordings with a friendly message and drops out of the digests.
Writes are not touched: an enabled plugin's writes still go through the preview + confirm gate."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Plugin:
    name: str                                   # the key used in config [plugins] and in messages
    commands: tuple[str, ...] = ()              # slash commands it owns
    intents: tuple[str, ...] = ()               # language-layer intents and pending-write intents it owns
    tools: tuple[str, ...] = ()                 # agent-mode tools it owns
    digest_sections: tuple[str, ...] = ()       # digest parts it contributes
    requires: tuple[str, ...] = ()              # plugins it cannot work without


REGISTRY: dict[str, Plugin] = {p.name: p for p in (
    Plugin("calendar",
           commands=("/bugun", "/yarin", "/hafta", "/dogumgunleri"),
           intents=("add_event", "edit_event", "edit_events", "delete_event", "move_event", "list_events", "birthdays"),
           tools=("calendar_range", "find_events", "propose_add_event", "propose_edit_event", "propose_delete_event",
                  "show_draft", "edit_draft", "commit_draft"),
           digest_sections=("events", "birthdays")),
    Plugin("tasks",
           commands=("/gorevler",),
           intents=("task_write",),
           tools=("task_add", "task_set_due", "task_list", "task_done"),
           digest_sections=("tasks", "task_nag")),
    Plugin("lists",
           commands=("/liste", "/alinacaklar"),
           intents=("list_add", "list_clear", "list_remove"),
           tools=("list_get", "list_add", "list_check", "list_remove"),
           digest_sections=("lists",)),
    Plugin("reminders", digest_sections=("event_reminders",), requires=("calendar",)),
    Plugin("digests", digest_sections=("morning", "evening", "plan")),
)}
NAMES = tuple(REGISTRY)


class PluginConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Plugins:
    """The switch board. `disabled` holds the names switched off in the config."""
    disabled: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_raw(cls, raw: dict | None) -> "Plugins":
        off = set()
        for name, on in (raw or {}).items():
            if name not in REGISTRY:
                raise PluginConfigError(f"unknown plugin {name!r}, expected one of {', '.join(NAMES)}")
            if not isinstance(on, bool):
                raise PluginConfigError(f"plugins.{name} must be true or false")
            if not on:
                off.add(name)
        return cls(frozenset(off))

    def enabled(self, name: str) -> bool:
        """On when not switched off and every plugin it requires is on too."""
        return name not in self.disabled and all(self.enabled(r) for r in REGISTRY[name].requires)

    def off_name(self, name: str) -> str:
        """The plugin to blame in a refusal: the switched-off dependency, or the plugin itself."""
        for r in REGISTRY[name].requires:
            if not self.enabled(r):
                return r
        return name

    def owner_of(self, kind: str, value: str) -> str | None:
        """The plugin that owns a command, intent or tool name (kind: commands | intents | tools), or None for core."""
        for p in REGISTRY.values():
            if value in getattr(p, kind):
                return p.name
        return None

    def blocked(self, kind: str, value: str) -> str | None:
        """The plugin to name in a refusal when `value` belongs to a switched-off plugin, else None."""
        owner = self.owner_of(kind, value)
        return self.off_name(owner) if owner and not self.enabled(owner) else None

    def summary(self) -> list[tuple[str, bool]]:
        return [(n, self.enabled(n)) for n in NAMES]
