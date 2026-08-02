# Doc Code

[Leia em português](README-PTBR.md)

`doc-code` generates documentation for Python, JavaScript, and TypeScript files using OpenAI, Gemini, or Ollama.

By default, it displays a reviewable diff of the generated documentation. Use `--no-show-diff` to keep only the compact summary. To write the changes, run:

```shell
doc-code --output apply
```

For automation, combine `--yes` with `--output apply`. Each file is written as soon as its generation and validation complete; a later failure does not roll back files that were already applied. Immediately before writing, the file is compared with the preview. If it changed, `doc-code` does not overwrite it. With `confirm = true`, Doc Code shows and confirms each formatted docstring before applying it; with `confirm = false`, it reviews the complete generated file diff before applying it.

## Installation and requirements

`doc-code` requires Python 3.11 or later and Git on your `PATH`. To process JavaScript, install Node.js. For TypeScript/TSX, also install the `tsc` compiler (for example, `npm install --global typescript`). These executables validate generated syntax before any file is changed.

For development, run `uv sync --locked --all-extras`. To install the published package, run `pip install doc-code`.

## Usage

```shell
doc-code                         # Git changes: staged, unstaged, and untracked files
doc-code src/a.py src/b.ts       # one or more specific files or directories
doc-code --selection repository  # every eligible file
doc-code --coverage all --format numpy
doc-code --language Portuguese
doc-code --request-scope symbol
doc-code --no-show-diff          # compact preview without the unified diff
doc-code --check                 # fail CI when symbols are undocumented
doc-code --continue-on-error     # return status 0 when a file is skipped
doc-code config init
doc-code config show
```

Configuration uses this precedence: flags, `--config`, `DOC_CODE_*` environment variables, `.doc-code.toml`, user configuration, and defaults. Set `language = "Portuguese"` in `[documentation]`, use `DOC_CODE_LANGUAGE`, or pass `--language Portuguese` to control the generated language. Credentials use `OPENAI_API_KEY` or `GEMINI_API_KEY`; Ollama needs no credential.

## Configuration (`.doc-code.toml`)

Create a starter file with `doc-code config init`. The `[ai]`, `[documentation]`, and `[limits]` sections are merged into one configuration. The following are all accepted keys.

### `[ai]`

| Option | Values / default | Purpose |
| --- | --- | --- |
| `provider` | `openai`, `gemini`, or `ollama` · `ollama` | Selects the provider that generates descriptions. |
| `model` | string · provider-dependent | Single model used when `models` is empty: `qwen2.5-coder:14b`, `gpt-5.6-sol`, or `gemini-3.6-flash`. |
| `models` | list of up to 3 strings · `qwen2.5-coder:14b`, `gemma4:e4b` | Candidates rotated across attempts; takes precedence over `model`. An empty list uses only `model`. |
| `endpoint` | URL or absent · provider default endpoint | Overrides the provider endpoint. Authenticated providers require HTTPS outside loopback. |
| `max_input_tokens` | positive integer · `12000` | Estimated limit for the prompt sent to the model. |
| `context_window_tokens` | positive integer · `32768` | Total model context window; must fit input and output. |
| `max_output_tokens` | positive integer · `800` | Limit for generated response tokens. |
| `temperature` | number · `0.2` | Controls variation; must be non-negative and is between 0 and 2 for OpenAI and Gemini. |
| `timeout_seconds` | positive integer · `60` | Maximum duration of each provider call. |

`max_input_tokens + max_output_tokens` cannot exceed `context_window_tokens`.

### `[documentation]`

| Option | Values / default | Purpose |
| --- | --- | --- |
| `selection` | `changes`, `repository` · `changes` | Chooses Git changes or every eligible file. |
| `coverage` | `missing`, `minimal`, `all` · `missing` | `missing` includes every undocumented symbol; `minimal` includes only undocumented modules and top-level public APIs; `all` generates or replaces documentation for every symbol. |
| `request_scope` | `file`, `symbol` · `file` | Chooses the context for each call: a complete file or one symbol. It does not change which symbols are eligible. |
| `language` | non-empty string · `English` | Language requested for generated descriptions. |
| `python_format` | `google`, `numpy`, `sphinx` · `google` | Parameter-section style for Python docstrings. |
| `javascript_format` | `jsdoc` · `jsdoc` | Annotation style for JavaScript and TypeScript. |
| `output` | `preview`, `apply` · `preview` | Shows a preview or writes changes. |
| `confirm` | boolean · `true` | `true` shows and confirms each formatted docstring before applying it; `false` shows the generated file diff and confirms it before applying it. |

### `[limits]`

| Option | Values / default | Purpose |
| --- | --- | --- |
| `max_files_per_request` | positive integer · `50` | Maximum number of files in a scope. |
| `max_file_bytes` | positive integer · `100000` | Maximum size of each processed file. |
| `exclude` | list of glob patterns | Removes files from scope. Defaults exclude dependencies, build artifacts, minified files, and `package-lock.json`. |
| `include` | list of glob patterns · empty list | When provided, keeps only files matching at least one pattern. |

## Python docstring style

Generated Python docstrings follow PEP 257: summaries end in a period; multiline docstrings have a blank line before and after sections, with closing quotes on their own line. Module and class docstrings are separated from the next declaration by one blank line. Empty lines have no indentation whitespace.

Line length also follows the target project. For Python, `doc-code` uses `tool.ruff.line-length` and `tool.ruff.lint.pydocstyle.convention` from the nearest `pyproject.toml` (or 88 and the configured `doc-code` format when absent). Long summaries are separated from the description by a blank line according to D205. For JavaScript and TypeScript, it uses `max-len` from the nearest `eslint.config.js`, `eslint.config.mjs`, or `eslint.config.cjs` (or 100 when no rule exists). Descriptions and JSDoc lines are wrapped while accounting for indentation and delimiters.

The `google`, `numpy`, and `sphinx` styles affect only the parameter section. To rewrite existing docstrings using one of these styles, run:

```shell
doc-code --coverage all --output apply --yes
```

For functions and methods, `doc-code` asks the model for a distinct description for every parameter and inserts it in the corresponding parameter section. The structured response must include every requested symbol and parameter; incomplete responses are rejected and generated again. Legacy integrations retain a fallback text for compatibility.

By default, `request_scope = "file"` sends the file and all symbols that require generation in a single request. Use `request_scope = "symbol"` (or `--request-scope symbol`) to send each symbol in a separate request with only its code scope. This reduces context in large files but can increase latency and the number of calls. For modules, it sends a structural index — opening docstring, imports, constants, and public signatures — without bodies. With `output = "apply"` and `confirm = true`, each formatted docstring is reviewed and confirmed individually; a rejected docstring is reported and skipped while the remaining symbols continue. `--yes` applies without prompts. With `confirm = false`, the full file diff is reviewed before any of its changes are written.

Regardless of `request_scope`, `coverage` decides which symbols are eligible and `request_scope` only changes the context unit sent to the model. Use `coverage = "all"` to rewrite existing documentation.

Partial failures return status 1 after all remaining eligible files have been processed. Use `--continue-on-error` only when automation explicitly accepts partial results.
