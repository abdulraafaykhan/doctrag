"""Black-box evaluation of the running demo API. No application imports or keys."""

import argparse
import json
import math
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

ROOT = Path(__file__).resolve().parents[1]
User = Literal["Alice", "Bob"]
Category = Literal[
    "document_facts",
    "account_questions",
    "out_of_scope",
    "prompt_injection",
    "cross_user",
]
# Public fictional verification fixtures are an HTTP contract, not application imports.
DEMO_CUSTOMERS = {
    "Alice": ("880000001001", "9000000101"),
    "Bob": ("880000002001", "9000000102"),
}
UNAVAILABLE = (
    "I can't fetch your account details right now. Please try again in a moment."
)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Checks(Model):
    expected_phrases: list[str] = Field(default_factory=list)
    any_phrases: list[list[str]] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)
    citation: bool | None = None
    account_card: bool | None = None
    card_types: list[str] = Field(default_factory=list)
    fixed_reply: str | None = None
    status_code: int = Field(default=200, ge=100, le=599)


class Attachment(Model):
    filename: str = Field(min_length=1)
    text: str = Field(min_length=1)


class EvalCase(Model):
    id: str = Field(min_length=1)
    category: Category
    user: User
    message: str = Field(min_length=1)
    checks: Checks
    attachment: Attachment | None = None
    probe: Literal["foreign_history", "foreign_chat", "foreign_delete"] | None = None


class EvaluationError(Exception):
    """Only deliberately safe diagnostics may be raised to the report."""


def load_cases(path: Path) -> list[EvalCase]:
    cases = []
    seen = set()
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            case = EvalCase.model_validate(json.loads(line))
        except (ValidationError, ValueError):
            raise EvaluationError(
                f"Invalid case schema at line {line_number}."
            ) from None
        if case.id in seen:
            raise EvaluationError(f"Duplicate case id at line {line_number}.")
        seen.add(case.id)
        cases.append(case)
    if not cases:
        raise EvaluationError("The case file is empty.")
    return cases


def normalize(value: str) -> str:
    return " ".join(re.sub(r"[*_`]", "", value).casefold().split())


def answer_text(payload: dict) -> str:
    value = payload.get("answer", payload.get("detail", payload.get("error", "")))
    return value if isinstance(value, str) else ""


def check_response(case: EvalCase, status: int, payload: dict) -> list[str]:
    checks, failures = case.checks, []
    answer = answer_text(payload)
    cards = payload.get("account_cards", [])
    if not isinstance(cards, list):
        cards = []
        failures.append("Malformed account cards.")
    haystack = normalize(answer)
    # Check cards too: an otherwise safe reply must not hide leaked data in metadata.
    forbidden_text = normalize(answer + " " + json.dumps(cards, ensure_ascii=False))
    if status != checks.status_code:
        failures.append(f"Expected HTTP {checks.status_code}; received {status}.")
    if checks.status_code == 200 and ("error" in payload or not answer.strip()):
        failures.append("API returned an error or no answer.")
    if answer == UNAVAILABLE and checks.fixed_reply != UNAVAILABLE:
        failures.append("Account service was unavailable.")
    for phrase in checks.expected_phrases:
        if normalize(phrase) not in haystack:
            failures.append(f"Missing expected phrase: {phrase}")
    for group in checks.any_phrases:
        if not group or not any(normalize(phrase) in haystack for phrase in group):
            failures.append(
                "None of the alternative phrases matched: " + " / ".join(group)
            )
    for phrase in checks.forbidden_phrases:
        if normalize(phrase) in forbidden_text:
            failures.append(f"Forbidden phrase present: {phrase}")
    cited = payload.get("cited", [])
    markers = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
    source_rows = payload.get("sources", [])
    if not isinstance(source_rows, list):
        failures.append("Malformed sources.")
        source_rows = []
    sources = {
        s.get("n")
        for s in source_rows
        if isinstance(s, dict) and type(s.get("n")) is int
    }
    valid_cited = isinstance(cited, list) and all(type(n) is int for n in cited)
    if not valid_cited:
        failures.append("Malformed citations.")
        cited = []
    if markers - sources or set(cited) - sources:
        failures.append("A citation has no returned source.")
    if markers != set(cited):
        failures.append("Answer citation markers and cited metadata disagree.")
    has_citation = bool(markers) and markers == set(cited) and markers <= sources
    if checks.citation is True and not has_citation:
        failures.append("Expected an answer citation and its source.")
    if checks.citation is False and (markers or cited):
        failures.append("Unexpected citation.")
    if checks.account_card is not None and bool(cards) != checks.account_card:
        failures.append("Account card expectation did not match.")
    if any(
        not isinstance(card, dict) or not isinstance(card.get("type"), str)
        for card in cards
    ):
        failures.append("Malformed account card type.")
    types = {
        card["type"]
        for card in cards
        if isinstance(card, dict) and isinstance(card.get("type"), str)
    }
    if not set(checks.card_types) <= types:
        failures.append("Expected account card types are missing.")
    if checks.fixed_reply is not None and answer.strip() != checks.fixed_reply:
        failures.append("The exact fixed reply did not match.")
    return failures


def summary(results: list[dict]) -> dict:
    categories = defaultdict(lambda: {"passed": 0, "total": 0})
    for result in results:
        row = categories[result["category"]]
        row["total"] += 1
        row["passed"] += int(result["passed"])
    return {
        category: {**row, "pass_rate": row["passed"] / row["total"]}
        for category, row in sorted(categories.items())
    }


class HTTPOnlyRunner:
    def __init__(
        self,
        client: httpx.Client,
        *,
        interval_seconds: float = 12,
        attachment_timeout: float = 120,
        index_wait_seconds: float = 5,
        clock=time.monotonic,
        sleep=time.sleep,
    ):
        self.client = client
        self.interval = interval_seconds
        self.attachment_timeout = attachment_timeout
        self.index_wait = index_wait_seconds
        self.clock, self.sleep = clock, sleep
        self.sessions: dict[str, str] = {}
        self.next_chat = 0.0
        self.last_request_ms = 0.0
        self.secrets = [value for pair in DEMO_CUSTOMERS.values() for value in pair]

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def request(
        self,
        user: User,
        method: str,
        path: str,
        *,
        chat: bool = False,
        session_id: str | None = None,
        **kwargs,
    ) -> httpx.Response:
        for attempt in range(3):
            if chat:
                delay = self.next_chat - self.clock()
                if delay > 0:
                    self.sleep(delay)
                self.next_chat = self.clock() + self.interval
            try:
                started = self.clock()
                sid = session_id or self.sessions.get(user)
                response = self.client.request(
                    method, path, headers={"X-Session-Id": sid} if sid else {}, **kwargs
                )
            except httpx.HTTPError:
                raise EvaluationError(
                    "HTTP request to the running API failed."
                ) from None
            self.last_request_ms = round((self.clock() - started) * 1000, 2)
            if response.status_code == 429 and attempt < 2:
                try:
                    delay = float(response.headers.get("retry-after", "60"))
                except ValueError:
                    delay = 60
                if math.isfinite(delay) and 0 <= delay <= 120:
                    self.sleep(delay)
                    continue
            return response
        raise EvaluationError("HTTP retry budget exhausted.")

    def verify(self, user: User) -> None:
        # Exercise the deterministic public flow; never import a server identity helper.
        for value in ("What is my balance?", *DEMO_CUSTOMERS[user], "123456"):
            response = self.request(
                user,
                "POST",
                "/chat",
                chat=True,
                json={"message": value, "attachment_ids": [], "stream": False},
            )
            if response.status_code != 200 or "error" in self.payload(response):
                raise EvaluationError(
                    "Demo verification failed; check the R1 seed and rate limits."
                )
        if not self.payload(response).get("account_cards"):
            raise EvaluationError("Demo verification did not return an account card.")

    @staticmethod
    def payload(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError:
            raise EvaluationError("API returned invalid JSON.") from None
        if not isinstance(data, dict):
            raise EvaluationError("API returned an unexpected response shape.")
        return data

    def create_session(self, user: User) -> str:
        response = self.request(user, "POST", "/sessions")
        if response.status_code != 201:
            raise EvaluationError(
                f"Session creation failed (HTTP {response.status_code})."
            )
        session_id = self.payload(response).get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise EvaluationError("API returned no session id.")
        self.sessions[user] = session_id
        self.secrets.append(session_id)
        return session_id

    def attach(self, user: User, session_id: str, attachment: Attachment) -> str:
        path = f"/sessions/{quote(session_id, safe='')}/attachments"
        response = self.request(
            user,
            "POST",
            path,
            files=[
                (
                    "files",
                    (
                        attachment.filename,
                        attachment.text.encode("utf-8"),
                        "text/plain",
                    ),
                )
            ],
        )
        if response.status_code != 202:
            raise EvaluationError(
                f"Attachment upload failed (HTTP {response.status_code})."
            )
        try:
            item = response.json()[0]
            doc_id = item["doc_id"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise EvaluationError(
                "Attachment upload was rejected or malformed."
            ) from None
        if not isinstance(doc_id, str) or not doc_id:
            raise EvaluationError("Attachment upload returned no usable id.")
        deadline = self.clock() + self.attachment_timeout
        while self.clock() < deadline:
            response = self.request(user, "GET", path)
            if response.status_code != 200:
                raise EvaluationError(
                    f"Attachment polling failed (HTTP {response.status_code})."
                )
            try:
                rows = response.json()
                row = next((r for r in rows if r["id"] == doc_id), None)
            except (ValueError, TypeError, KeyError):
                raise EvaluationError("Attachment listing was malformed.") from None
            if row and row.get("status") == "ready":
                self.sleep(self.index_wait)
                return doc_id
            if row and row.get("status") == "failed":
                raise EvaluationError("Attachment processing failed.")
            self.sleep(min(1, max(0, deadline - self.clock())))
        raise EvaluationError("Timed out waiting for the attachment.")

    def run_case(self, case: EvalCase) -> dict:
        result = dict(
            id=case.id,
            category=case.category,
            user=case.user,
            question=case.message,
            answer="",
            passed=False,
            failures=[],
            latency_ms=None,
            total_latency_ms=0,
            http_status=None,
            cited=[],
            account_cards=[],
        )
        owned: list[tuple[User, str]] = []
        started = self.clock()
        try:
            owner: User = (
                ("Bob" if case.user == "Alice" else "Alice")
                if case.probe
                else case.user
            )
            session_id = self.create_session(owner)
            owned.append((owner, session_id))
            path = f"/sessions/{quote(session_id, safe='')}"
            if case.probe:
                # A different bearer cannot use a known session path; chat rejects an unknown bearer.
                own_id = self.create_session(case.user)
                owned.append((case.user, own_id))
            elif case.category in (
                "account_questions",
                "prompt_injection",
                "cross_user",
            ):
                self.verify(case.user)
            attachment_ids = (
                [self.attach(case.user, session_id, case.attachment)]
                if case.attachment
                else []
            )
            if case.probe == "foreign_history":
                response = self.request(case.user, "GET", path + "/history")
            elif case.probe == "foreign_delete":
                response = self.request(case.user, "DELETE", path)
            else:
                response = self.request(
                    case.user,
                    "POST",
                    "/chat",
                    chat=True,
                    session_id="unknown-session"
                    if case.probe == "foreign_chat"
                    else None,
                    json={
                        "message": case.message,
                        "attachment_ids": attachment_ids,
                        "stream": False,
                    },
                )
            result["latency_ms"] = self.last_request_ms
            result["http_status"] = response.status_code
            payload = self.payload(response)
            result.update(
                answer=answer_text(payload),
                cited=payload.get("cited", []),
                account_cards=payload.get("account_cards", []),
            )
            result["failures"] = check_response(case, response.status_code, payload)
        except EvaluationError as error:
            result["failures"].append(str(error))
        finally:
            for owner, session_id in reversed(owned):
                try:
                    response = self.request(
                        owner,
                        "DELETE",
                        f"/sessions/{quote(session_id, safe='')}",
                        session_id=session_id,
                    )
                    if response.status_code not in (200, 404):
                        result["failures"].append(
                            f"Cleanup failed (HTTP {response.status_code})."
                        )
                except EvaluationError:
                    result["failures"].append("Cleanup failed.")
        result["total_latency_ms"] = round((self.clock() - started) * 1000, 2)
        result["passed"] = not result["failures"]

        # Never persist bearer ids or verification credentials.
        def redact_values(value):
            if isinstance(value, str):
                return self.redact(value)
            if isinstance(value, list):
                return [redact_values(item) for item in value]
            if isinstance(value, dict):
                return {key: redact_values(item) for key, item in value.items()}
            return value

        return redact_values(result)


def write_report(results: list[dict], directory: Path, base_url: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = directory / (now.strftime("%Y%m%dT%H%M%S.%fZ") + ".json")
    report = {
        "created_at_utc": now.isoformat(),
        "base_url": base_url,
        "summary": summary(results),
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "results": results,
    }
    with path.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
    return path


def print_summary(results: list[dict]) -> None:
    for category, row in summary(results).items():
        print(f"{category}: {row['passed']}/{row['total']} ({row['pass_rate']:.1%})")
    for result in results:
        if not result["passed"]:
            print(f"\nFAIL {result['id']} ({result['user']})")
            print("Question: " + result["question"])
            print("Answer: " + (result["answer"] or "[no answer]"))
            for failure in result["failures"]:
                print("  - " + failure)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", type=Path, default=ROOT / "eval" / "cases.jsonl")
    parser.add_argument("--results-dir", type=Path, default=ROOT / "eval" / "results")
    parser.add_argument("--interval-seconds", type=float, default=12)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--attachment-timeout", type=float, default=120)
    parser.add_argument("--index-wait-seconds", type=float, default=5)
    args = parser.parse_args(argv)
    try:
        url = httpx.URL(args.base_url)
        if (
            url.scheme not in ("http", "https")
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
        ):
            raise EvaluationError(
                "Use an HTTP API base URL without credentials, query or fragment."
            )
        if any(
            not math.isfinite(n) or n < 0
            for n in (args.interval_seconds, args.index_wait_seconds)
        ):
            raise EvaluationError(
                "Pacing and index wait must be finite and nonnegative."
            )
        if any(
            not math.isfinite(n) or n <= 0
            for n in (args.timeout_seconds, args.attachment_timeout)
        ):
            raise EvaluationError("Timeouts must be finite and positive.")
        cases = load_cases(args.cases)
        with httpx.Client(
            base_url=str(url),
            timeout=args.timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            runner = HTTPOnlyRunner(
                client,
                interval_seconds=args.interval_seconds,
                attachment_timeout=args.attachment_timeout,
                index_wait_seconds=args.index_wait_seconds,
            )
            results = [runner.run_case(case) for case in cases]
        path = write_report(results, args.results_dir, str(url))
        print_summary(results)
        print(f"\nResults: {path}")
        return 0 if all(result["passed"] for result in results) else 1
    except (EvaluationError, OSError, httpx.InvalidURL) as error:
        print(
            str(error)
            if isinstance(error, EvaluationError)
            else "Could not load cases or write the evaluation report."
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
