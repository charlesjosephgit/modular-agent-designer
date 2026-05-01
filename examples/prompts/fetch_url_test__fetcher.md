Fetch this URL using the tool named `fetch_url`: {{state.user_input.url}}

Rules:
- Call only `fetch_url`.
- Pass the URL exactly as provided.
- If the tool returns an error string, return that error and stop.
- Otherwise, return a concise summary of the fetched page and include the first 500 characters of raw fetched text.
