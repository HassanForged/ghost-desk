PASS 31 / FAIL 0 / NOT RUN 0

| id | result | command | actual output |
| --- | --- | --- | --- |
| B1 | PASS | ghost_boot.run_boot False then True | first=3 second=5 checks_in_second=False |
| B2 | PASS | Ctrl-Break ghost_boot.py ; echo cursor | code=130 echo=cursor cursor_restored=True |
| B3 | PASS | findstr hood ghost-boot-preview.html | image=True forbidden_drawing=False |
| P1 | PASS | run_turn build a one-page site | reason=plan_wait files=0 snippet=Plan (waiting for approval). I will not write files until you approve. |
| P2 | PASS | plan text contains address | Plan (waiting for approval). Checklist: - build a one-page site for hassan@example.com |
| P3 | PASS | run_turn make it feel right | Plan (waiting for approval). Checklist: - make it feel right, I can't explain it. |
| P4 | PASS | run_turn build the site after email | Plan (waiting for approval). Checklist: - build a one-page site for hassan@example.com |
| H1 | PASS | typo email rewritten | file=<p>hassan@example.com</p> |
| H2 | PASS | config model question | read=True reply=The model is stub-model-unique |
| H3 | PASS | missing path | C:\Users\Hassan\AppData\Local\Temp\ghost-stress\rerun\h3\work\no-such-file.txt is missing. |
| H4 | PASS | done with no file | Not done. Missing: index.html |
| C1 | PASS | run_turn after forced compact | pong |
| C2 | PASS | inspect rolling summary | Rolling summary of earlier turns: user: note 9 xxxx assistant: ack 9 yyyy |
| C3 | PASS | time after do-not-build | It is 3pm in Vancouver. |
| C4 | PASS | ask email after compact | hassan@example.com |
| C5 | PASS | type during compact | elapsed=0.03 reply=blue |
| C6 | PASS | use saved skill after compact | use the archive skill |
| C7 | PASS | search memory for COMPACTION | hit=False |
| M1 | PASS | new session name vs trading | The product is Ghost Desk. trading is not the job |
| M2 | PASS | new process memory email | hassan@example.com |
| M3 | PASS | build_digest | Recurring candidates - email: hassan@example.com - name: Ghost Desk |
| S1 | PASS | learn 12 tricks | new_top=['code', 'data', 'documents', 'media'] |
| S2 | PASS | find pdf under broad skill | pdf is under documents |
| G1 | PASS | two ghosts same file | part A |
| G2 | PASS | resume half file | <p>half done |
| G3 | PASS | done without files | Not done. Missing: a.html, b.html |
| G4 | PASS | child prompt | alpha present, SECRET CHAT absent |
| R1 | PASS | keys x 9 0 6 then 2 | returned=2 |
| R2 | PASS | setup boot 3 fake 403 | auth=api_key http=1 line=access_denied |
| R3 | PASS | setup boot 5 | prompts=base_url [http://127.0.0.1:11434/v1]: model [llama3.2]: auth=local |
| R4 | PASS | search setup output for test secret | hit=False |

## FAILs

No remaining FAILs.

First-pass failures were fixed once and rerun. The files changed were `ghost_boot.py`, `src/ghost_desk/plan.py`, `src/ghost_desk/verify.py`, `src/ghost_desk/agent.py`, `src/ghost_desk/compact.py`, `src/ghost_desk/background.py`, `src/ghost_desk/skills.py`, `src/ghost_desk/permissions.py`, `src/ghost_desk/tools.py`, and `src/ghost_desk/subagents.py`.
