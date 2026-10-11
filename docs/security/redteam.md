# Red team: prompt injection, archives, code execution (phase 5, task 2)

Method: attack fixtures run through the **real** prompt builders, extractor and analysers; the model is a recording
scripted client, so what is asserted is what the builders put on the wire, not what a model happens to do with it.
Every row names the test that pins it. "Result" is what the test run on 2026-10-11 returned.

Tests: `backend/tests/security/test_redteam_prompt_injection.py`,
`backend/tests/security/test_archive_limits_recheck.py`,
`backend/tests/security/test_repository_code_is_never_executed.py` (all in `uv run pytest`, no data store needed) and
`backend/tests/integration/test_upload_limits_redteam.py` (`-m integration`, real API on real stores).

## 1. Prompt injection

The hostile repository (`_hostile_files` in the test) carries, in its README, CHANGELOG, a docstring, comments, a
function name and the reader preference: an instruction override, a markdown image that would exfiltrate, a forged
tool call, chat-template tokens, a commit-like "SYSTEM OVERRIDE" block, a secret, and twelve spellings of a fence
tag. It is analysed in memory and driven through planner refine, research decompose, writer, critique, rewrite and
chat. For every recorded request the tests assert: the policy is in `system`; no secret is in the request; hostile
text appears only inside `<repository_data>` fences; no fence can be closed from inside (on the exact tag and on a
normalised view); hostile text really reached that prompt.

| Attack | Control | Test | Result |
|---|---|---|---|
| "Ignore previous instructions" in README, comment, docstring, CHANGELOG, symbol name | repository text is fenced as data; `UNTRUSTED_POLICY` in every system prompt | `test_every_prompt_is_policy_bearing_redacted_and_fenced[guide.plan, guide.research, guide.section, guide.critique, guide.revise, chat.answer]` | pass (after F-2, F-3) |
| Exfiltration through `![x](https://evil/...)` in repository text | fenced as data in the prompt; exports break `](` and `][` and neutralise reference definitions, autolinks and raw HTML | same, then `test_echoed_hostile_output_is_inert_in_the_markdown_exports`, `test_echoed_hostile_output_is_inert_in_the_print_html` | pass |
| Model obeys the repository and echoes the payload into a chapter, then into critique and rewrite | the echoed chapter is fenced again as `repository_derived` in the critique and rewrite prompts | the `EchoLLM` run behind the first row (critique and revise requests carry the echo) | pass |
| Forged tool call JSON, `<|im_start|>system`, `[INST]`-style tokens | data inside a fence; the guide and chat paths send no tool definitions | `..._fenced[...]` (markers outside a fence fail the test) | pass |
| Fence-breaking: `</repository_data>`, upper case, spaced, newline inside, attributes | `strip_fence_tags` | `test_no_spelling_of_a_fence_tag_survives_inside_a_fence[...]` (12 closers, 2 openers) | pass (spaced variant fixed, F-1) |
| Look-alike closers: zero-width characters inside the tag, Cyrillic letters, fullwidth or small angle brackets and low line | `strip_fence_tags` judges anything bracket-shaped on its NFKC-normalised, format-character-free, look-alike-folded form | same test | pass (fixed, F-1) |
| Closer or newline smuggled in a file path or symbol that is shown *outside* the fence in an evidence header | `safe_header_field`: one line, control characters removed, same tag stripping, 200 characters | `test_a_hostile_path_or_symbol_cannot_inject_a_tag_or_a_line_into_a_header` | pass (F-1) |
| Instruction hidden in a file name or symbol that stays visible in a header | `UNTRUSTED_POLICY` now says paths, symbol names and chapter titles outside the tags are data too | covered by the policy assertion; the text itself is not machine-checkable | **residual**, see section 4 |
| Secret in a README, a code constant, the question or the history | `redact` before fencing, again at the gateway for every outgoing part; the retrieval notes are redacted too (F-4) | `test_the_secret_is_redacted_in_what_the_model_is_shown`, `test_every_prompt_is_policy_bearing_...` | pass (after F-4) |
| Second-order injection: a chapter title or objective written by the planner after reading the hostile repository | title and objective are rendered in a fenced `## Chapter topic` block in writer and rewrite prompts (F-2) | `..._fenced[guide.section]`, `[guide.revise]` | pass (after F-2) |
| Hostile reader preference | fenced as `user_preference` in the planner too (F-3); already fenced in writer and rewrite | `..._fenced[guide.plan]` | pass (after F-3) |
| Echoed payload in Markdown export: images, inline and reference links, definitions, autolinks, raw HTML, `[t]\n(url)` | `_neutralise` (`exports/markdown.py`) | `test_echoed_hostile_output_is_inert_in_the_markdown_exports` | pass |
| Echoed payload in the print HTML (PDF source) | escape first, then a closed set of tags (`exports/render.py`); no `<img>`, `<a>` off-page, `<script>`, event attributes | `test_echoed_hostile_output_is_inert_in_the_print_html` | pass |

## 2. Archives and URL ingestion

| Attack | Control | Test | Result |
|---|---|---|---|
| Zip bomb by ratio (zeros) | per-entry and total ratio on bytes actually read | `test_ratio_is_enforced_on_bytes_actually_read`, first-pass `test_zip_bomb_ratio_rejected` | pass |
| Header lies: declared size smaller or larger than the stream | sizes enforced while streaming; CRC | `test_a_header_that_understates_the_size_cannot_smuggle_a_big_file`, `test_a_header_that_overstates_the_size_is_not_trusted_either` | pass (typed error, nothing left on disk) |
| Huge single file | recorded in `oversized`, not written, the rest is extracted | `test_huge_single_file_is_recorded_not_written_and_the_rest_extracts`, `test_tar_declared_size_larger_than_limit_is_not_written` | pass |
| File-count limit evaded with directory or symlink entries (zip) | entry count (files, directories, links) capped at 2x the file limit, as for tar (F-5) | `test_a_flood_of_directory_entries_is_refused_like_a_flood_of_files`, `test_a_flood_of_symlink_entries_is_refused`, `test_tar_entry_flood_is_refused_without_extracting` | pass (after F-5) |
| Depth | exact component limit | `test_depth_limit_is_exact` | pass |
| `..`, absolute, drive letter, UNC, backslash and mixed separators, NUL | `normalize_member_path` | `test_unsafe_member_names_are_rejected[8 spellings]`, first-pass `test_rejects_traversal_and_absolute` | pass |
| Write through a symlink entry | links are skipped, never created | `test_a_file_written_through_a_skipped_symlink_name_stays_inside` | pass |
| Hardlink, device, FIFO, absolute tar member | skipped and recorded, or rejected | `test_tar_hardlinks_devices_fifos_and_absolute_names` | pass |
| Nested archives | an inner archive is a plain file; never opened, never recursed | `test_nested_archives_are_never_opened`, `test_a_zip_inside_a_tar_is_a_plain_file` | pass |
| Not an archive / gzip that is not a tar | typed `InputError` | `test_gzip_that_is_not_a_tar_is_a_typed_error`, first-pass `test_not_a_zip` | pass |
| URL tricks: userinfo, look-alike host, port, `http`, API/codeload host, `..`, blob/tree-less paths, `file:`/`ssh:`, dotless-i | `parse_github_url` | `test_look_alike_and_non_repository_urls_are_refused[14 URLs]` | pass |
| Redirect from codeload to another host | request hook refuses any host outside `api.github.com` and `codeload.github.com` | `test_a_redirect_to_another_host_is_refused` | pass (the second request is never sent) |
| Endless response body | streamed byte cap | `test_an_endless_body_is_cut_at_the_size_limit` | pass |
| Hostile tarball served by the download path | same `safe_extract`; nothing left in the work directory | `test_a_hostile_tarball_from_the_download_is_rejected_and_leaves_no_source` | pass |
| Private or oversized repository | refused before any download | `test_a_private_or_oversized_repository_is_refused_before_download` | pass |
| Upload over `max_zip_bytes`; empty, text, executable or gzip body on the ZIP route | cap enforced while streaming (413 `INPUT_TOO_LARGE`), magic bytes checked (400 `ZIP_INVALID`); no project row is created by any of them | `tests/integration/test_upload_limits_redteam.py::test_oversized_and_malformed_uploads_are_refused_and_leave_nothing_behind` | pass |
| Path-like upload file name; instruction over 1000 characters | name reduced to its last component; form field capped | `::test_filename_and_instruction_cannot_smuggle_paths_or_oversized_text` | pass |

The in-worker rejection of traversal and bomb archives (`FAILED_SECURITY`, nothing persisted) is covered by
`tests/integration/test_pipeline.py::test_path_traversal_archive_is_rejected_by_the_worker` and
`::test_zip_bomb_is_rejected_by_the_worker`.

## 3. Uploaded code is never executed

| Claim | Control | Test | Result |
|---|---|---|---|
| No module can spawn a process, import dynamically, eval or exec, unpickle, call ctypes, build a template from a string, load unsafe YAML or extract an archive outside `safe_extract` | AST scan over every module of `src/unfold` with a reasoned allow-list (two fixed-argv spawns of our own worker, Playwright in `exports.pdf`) | `test_no_module_can_run_or_load_repository_code`, `test_the_allow_list_is_still_needed`, `test_the_scanner_itself_catches_what_it_claims_to` | pass |
| A repository made of traps (import-time code, `setup.py`, `conftest.py`, `sitecustomize.py`, `.pth`, npm scripts, Makefile, Dockerfile, git hooks, notebook, Terraform `local-exec`, CI) goes through extract, scan, analyse, IR and chunking without anything firing | parse-only analysers | `test_a_repository_made_of_traps_is_analysed_without_running_anything`, `test_trap_files_are_inert_text_in_the_inventory` | pass: the canary directory stayed empty, nothing was imported, nothing was written next to the test |

The existing `test_architecture.py::test_no_subprocess_or_eval_of_repository_content` only checked `import subprocess`
and bare `eval`/`exec`; it stays, and the scan above supersedes it.

## 4. Defects found and fixed

| Id | Finding | Fix | Test that failed first |
|---|---|---|---|
| F-1 | Fence-tag neutralisation matched only the exact ASCII tag: a spaced `< / repository_data >`, a tag with zero-width characters, Cyrillic look-alike letters or fullwidth brackets survived inside a fence (and in evidence headers outside it) | `security/fence.py:25` `strip_fence_tags` (also used by `context/models.py:30` `safe_header_field`) | `test_no_spelling_of_a_fence_tag_survives_inside_a_fence[...]` (9 of 14 failed), `test_a_hostile_path_or_symbol_...` |
| F-2 | The writer and rewrite prompts put the chapter title and objective, which a model wrote after reading the repository, outside any fence, in the instruction part | `guide/write.py:199,216`, `guide/rewrite.py:149,168`: rendered in a fenced `## Chapter topic` block | `..._fenced[guide.section]`, `[guide.revise]` |
| F-3 | The planner put the reader preference outside a fence (writer and rewrite already fenced it) | `guide/plan.py:586` | `..._fenced[guide.plan]` |
| F-4 | Retrieval notes ("not found: <identifiers from the question>") were neither redacted nor fenced; a secret typed in a question was visible in the builder output (the gateway still redacted it on the wire) | `context/compiler.py:257` redacts the notes | `test_the_secret_is_redacted_in_what_the_model_is_shown` |
| F-5 | A zip with hundreds of thousands of directory entries was extracted in full: only files counted towards `max_file_count`, the tar path already capped entries | `ingestion/archive.py:219-220` | `test_a_flood_of_directory_entries_is_refused_like_a_flood_of_files` |

Reliability defects found by the same pass (R-1, R-2) are in `docs/design/reliability.md`.

The prompt text of the writer and rewriter changed (F-2). The offline rubric gate is unaffected (scripted model); a
live evaluation (`-m llm`) is the place to confirm chapter quality did not move.

## 5. Residual risks (stated, not fixed)

* A file name or symbol that reads as an instruction is still shown in an evidence header outside the fence, bounded
  to 200 characters and one line. The policy now tells the model those are data; a model can still be swayed.
  The header is what makes citations readable, so it stays.
* A bare URL in model output is not neutralised in the Markdown export (it is a click-through, not an auto-loaded
  image). Image, inline-link, reference-link and HTML syntax are neutralised.
* The scanner's `[Cf]`/look-alike set is a list, not the full Unicode confusables table: letters outside Cyrillic
  o/e/a/p/c/i/y/x would need adding if a model is observed to honour them.
* Chromium renders our escaped print HTML with JavaScript enabled (Mermaid needs it), offline and under a CSP; a
  Chromium escape is out of scope here.
* The full `claude-security` scan is deferred until phase 4 is merged.
