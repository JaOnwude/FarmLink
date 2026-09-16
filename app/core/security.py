"""
Cross-cutting auth utilities (JWT encode/decode, password hashing).

Lives in core/ rather than features/auth/ because multiple features depend
on it (every protected router needs get_current_user), and core/ is exactly
the place for things every feature shares - see CONTRIBUTING.md for the
core/ vs features/ rule.

Build target: Day 3
"""
# TODO (Day 3): create_access_token(), decode_token(), get_current_user()
# TODO (Day 3): password hashing via passlib[bcrypt]
