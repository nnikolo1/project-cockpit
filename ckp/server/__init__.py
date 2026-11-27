"""The HTTP plumbing of the viewer: request parsing, the token and cookie check, the routing table, JSON and file responses, startup and warm-up.
No logic lives here: every route calls one ckp.api function and sends its Result."""
