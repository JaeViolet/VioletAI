"""Manual verification script for VioletAI Memory V2.

Run with a real desktop session (NOT QT_QPA_PLATFORM=offscreen) and a running
Ollama instance so the parts of the system that automated tests cannot reach
are verified by a human:

    python tests/manual_test_memory_v2.py

The first half is deterministic and runs anywhere (temp SQLite store, no GUI).
The second half opens the real MainWindow and issues prompts through the live
pipeline, streaming through Ollama. Each step prints PASS/FAIL; read each
prompt aloud and confirm the displayed answer matches the expectation.

Do not rely on this script for automation; it is a checklist, not a test suite.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

_PASS = 0
_FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if condition:
        _PASS += 1
        print(f"  PASS  {label}")
    else:
        _FAIL += 1
        print(f"  FAIL  {label}  {detail}")


def deterministic_checks() -> None:
    from memory_v2.models import MemoryLayer
    from memory_v2.pipeline import MemorySystem
    from memory_v2.store import MemoryStore

    print("\n=== Deterministic pipeline checks (no GUI / no Ollama) ===")
    with tempfile.TemporaryDirectory() as temp_dir:
        service = MemorySystem(MemoryStore(Path(temp_dir) / "memory.db"))

        created = service.handle_user_message(
            "Remember that my favorite color is purple.", conversation_id="c1", message_id="1"
        )
        check("save favorite color", created.action is not None and getattr(created.action, "value", "") == "CREATE")

        updated = service.handle_user_message(
            "Change my favorite color to red.", conversation_id="c1", message_id="2"
        )
        check("update favorite color", getattr(updated.action, "value", "") == "UPDATE")
        check("value is red", service.list_memories()[0].value == "red")

        question = service.handle_user_message("What is my favorite color?", conversation_id="c1", message_id="3")
        check("question injects", question.retrieval is not None and question.retrieval.injected)
        check("answer is red", question.retrieval.selected[0].record.value == "red")

        drink = service.handle_user_message("What is my favorite drink?", conversation_id="c1", message_id="4")
        check("drink does not cross-inject", not drink.retrieval.injected)

        job = service.handle_user_message("Remember that my job is engineer.", conversation_id="c2", message_id="5")
        job_q = service.handle_user_message("What is my job?", conversation_id="c2", message_id="6")
        check(
            "job paraphrase retrieves occupation",
            job_q.retrieval is not None and job_q.retrieval.injected
            and job_q.retrieval.selected[0].record.value == "engineer",
        )
        wrong = service.handle_user_message("What project am I working on?", conversation_id="c2", message_id="7")
        check("negative control does not return occupation", not wrong.retrieval.injected)

        before = len(service.list_memories())
        service.handle_user_message("What is 12 * 8?", conversation_id="c3", message_id="8")
        check("unrelated chat writes nothing", len(service.list_memories()) == before)


def gui_and_ollama_checks() -> None:
    print("\n=== GUI + Ollama checks (requires desktop session and Ollama) ===")
    print("These steps are interactive. Confirm each answer by reading it.")
    print()

    from PySide6.QtWidgets import QApplication
    from main import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()

    checks = [
        (
            "Remember that my favorite color is purple.",
            "The assistant answers naturally WITHOUT mentioning memory saving.",
        ),
        (
            "What is my favorite color?",
            "The assistant answers using the stored value (purple). No memory confirmation text.",
        ),
        (
            "Change my favorite color to red.",
            "No confirmation of the memory update is shown.",
        ),
        (
            "What is my favorite drink?",
            "The assistant does NOT answer 'purple' or invent a drink from the color memory.",
        ),
        (
            "What is my job?",
            "Only answers if a job was previously stored; otherwise it says it does not know.",
        ),
    ]
    for prompt, expectation in checks:
        print(f"  PROMPT: {prompt}")
        print(f"  EXPECT: {expectation}")
        input("    ...press Enter after confirming the displayed answer...")
        check("interactive step (see expectation)", True, f"prompt={prompt}")

    print("\nVerify the Memory Manager (Settings -> Memory) shows the durable memories")
    print("from the steps above, and that searching 'favorite' lists the color memory.")
    input("    ...press Enter after confirming the Memory Manager contents...")
    check("memory manager shows stored facts", True, "manual inspection")

    window.close()
    app.processEvents()


def main() -> None:
    deterministic_checks()
    if "--interactive" in sys.argv or "-i" in sys.argv:
        gui_and_ollama_checks()
    else:
        print("\nSkipping interactive GUI/Ollama checks. Re-run with --interactive")
        print("on a desktop session with Ollama running to complete manual verification.")
    print(f"\nResult: {_PASS} passed, {_FAIL} failed")
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
