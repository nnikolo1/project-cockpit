"""The API layer: one module per group of what the Cockpit offers (state, queue, code, usage, settings, health, pages, actions).
Entry functions take a Context first and return a Result (ckp.api.result); the other functions of a module are the services under them."""
