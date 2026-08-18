<!--
  loom spec. ONE PAGE, HARD CAP 60 LINES. This file is injected verbatim into other agents'
  deny messages and conflict responses, so every line you add is a tax they pay on every clash.
  Fill EVERY [bracket] and delete none of the five *(mandatory)* headings; write `none` if empty.
  Node IDs are canonical: `relative/path.py::Class/method` (files: `relative/path.ext`).
  Run resolve_nodes on every ID BEFORE declare_plan. Spec discipline inspired by
  github/spec-kit (MIT, Copyright GitHub, Inc.).
-->

# Spec: [short imperative title, e.g. "Cache authenticate() results"]

**Agent**: [your agent id]  **Plan**: [plan_id, written back after declare_plan]  **Repo/branch**: [repo] / [branch]

## Goal *(mandatory)*

[Two sentences. Sentence 1: what changes and why, e.g. "Add a 60s TTL cache in front of
authenticate() so repeated logins skip the bcrypt round." Sentence 2: the observable outcome,
e.g. "Auth-heavy endpoints drop from ~120ms to <5ms on cache hit; behaviour is unchanged on miss."]

## Write targets *(mandatory)*

[Canonical node IDs you will EDIT. One per line. Must equal write_targets[] in declare_plan.]

- [src/auth/service.py::AuthService/authenticate]
- [src/auth/cache.py  — file-level ID for a new or non-code file]

## New/changed interfaces *(mandatory)*

[EXACT signatures other agents may build against. Include the full signature and return type;
mark each ADDED / CHANGED / UNCHANGED-BUT-LOAD-BEARING. Write `none` if you change no interface.
A blocked agent codes against THIS, never against your in-flight source.]

- CHANGED `AuthService.authenticate(self, email: str, password: str, *, use_cache: bool = True) -> AuthResult`
  (was `(self, email: str, password: str) -> AuthResult`; return type and raised exceptions unchanged)
- ADDED `AuthCache.get(key: str) -> AuthResult | None`
- ADDED `AuthCache.put(key: str, value: AuthResult, ttl_s: int = 60) -> None`

## Assumes *(mandatory)*

[Canonical node IDs you RELY ON but will NOT edit, each with the exact signature you rely on.
These become read claims — if someone changes them, you get warned. Write `none` if nothing.]

- [src/auth/models.py::AuthResult] — relies on `AuthResult(user_id: str, token: str, expires_at: datetime)` being a frozen dataclass
- [src/auth/hashing.py::verify_password] — relies on `verify_password(plain: str, hashed: str) -> bool`

## Out of scope *(mandatory)*

[One line. Name the adjacent ground you are NOT taking, so a peer can claim it safely, e.g.
"Session storage, token refresh, and the password-reset flow are untouched."]
