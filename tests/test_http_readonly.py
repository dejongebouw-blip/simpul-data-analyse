"""Toetst dat het clientoppervlak GET-only is.

De extractie mag Simpul niet wijzigen (US-7, SC-7). Deze module inspecteert
het publieke oppervlak van :class:`SimpulClient` en faalt zodra er iets
anders dan ``get`` aanroepbaar is: ``post``, ``put``, ``patch``, ``delete``,
of een generieke request-dispatcher.
"""
from __future__ import annotations

import inspect
import unittest
from typing import Any, Mapping, Optional

from simpul_extract.http_client import SimpulClient


class _NullTransport:
    """Transportstub die weigert te worden aangeroepen — oppervlakte-tests raken 'em nooit."""

    def get(self, url: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        raise AssertionError(
            "transport mag in oppervlakte-tests niet worden aangeroepen"
        )


def _make_client() -> SimpulClient:
    return SimpulClient(
        base_url="https://example.invalid",
        transport=_NullTransport(),
    )


FORBIDDEN_VERBS = (
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "request",
    "send",
)


class TestGetIsThePublicSurface(unittest.TestCase):
    def test_get_is_public_and_callable(self) -> None:
        client = _make_client()
        self.assertTrue(
            callable(getattr(client, "get", None)),
            msg="SimpulClient moet een aanroepbare `get` hebben",
        )

    def test_get_accepts_path_and_params(self) -> None:
        sig = inspect.signature(SimpulClient.get)
        params = [name for name in sig.parameters if name != "self"]
        self.assertIn("path", params)
        self.assertIn("params", params)

    def test_public_callable_surface_is_only_get(self) -> None:
        client = _make_client()
        public_callables = sorted(
            name
            for name in dir(client)
            if not name.startswith("_") and callable(getattr(client, name))
        )
        self.assertEqual(
            public_callables,
            ["get"],
            msg=(
                "Alleen `get` mag publiek en aanroepbaar zijn op de client; "
                f"gevonden: {public_callables}"
            ),
        )


class TestOtherVerbsAreForbidden(unittest.TestCase):
    def test_other_verbs_are_not_callable_on_instance(self) -> None:
        client = _make_client()
        for verb in FORBIDDEN_VERBS:
            with self.subTest(verb=verb):
                attr = getattr(client, verb, None)
                self.assertFalse(
                    callable(attr),
                    msg=(
                        f"SimpulClient.{verb} mag niet aanroepbaar zijn — "
                        "de HTTP-naad naar Simpul is strikt GET-only (US-7, SC-7)."
                    ),
                )

    def test_other_verbs_are_not_defined_on_class(self) -> None:
        for verb in FORBIDDEN_VERBS:
            with self.subTest(verb=verb):
                attr = getattr(SimpulClient, verb, None)
                self.assertFalse(
                    callable(attr),
                    msg=(
                        f"SimpulClient.{verb} mag niet als methode bestaan; "
                        "voeg geen HTTP-verb toe naast `get`."
                    ),
                )


class TestNoDispatchBackdoor(unittest.TestCase):
    """Geen doorgeefluik dat willekeurige methodenamen alsnog GET-lijkt."""

    def test_random_attribute_names_are_not_callable(self) -> None:
        client = _make_client()
        for name in (
            "post_incident",
            "delete_all",
            "update",
            "create",
            "arbitrary_verb",
            "call",
        ):
            with self.subTest(name=name):
                attr = getattr(client, name, None)
                self.assertFalse(
                    callable(attr),
                    msg=(
                        f"SimpulClient mag geen willekeurige `{name}` als "
                        "callable teruggeven; dat zou de GET-only naad ondermijnen."
                    ),
                )

    def test_client_instance_is_not_directly_callable(self) -> None:
        client = _make_client()
        self.assertFalse(
            callable(client),
            msg="SimpulClient-instantie mag niet als functie aangeroepen worden",
        )


if __name__ == "__main__":
    unittest.main()
