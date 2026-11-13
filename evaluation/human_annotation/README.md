# Human annotation display preview

Run from the SparkMe repository root with Python 3.10 or later.

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory evaluation/human_annotation
```

Open <http://127.0.0.1:8765>. Stop the server with Ctrl-C. To view a server running on a remote machine, forward its port to your workstation.

```bash
ssh -L 8765:127.0.0.1:8765 your-server
```

For direct access within an authorized network, replace `--bind 127.0.0.1` with the server's network interface address and open `http://SERVER_ADDRESS:8765`. Access depends on the server's firewall and network configuration. Serve the preview directory shown in the command so only its bundled static files are exposed.

The preview uses static HTML, CSS, and JavaScript with local assets and system fonts. It needs no package installation, model endpoint, GCP connection, or build step.

## Try the display

- Switch between conduct review and A/B comparison. Each example and mode retains its own draft answers and optional explanation while the page is open.
- Expand the extended scenario's full history, then collapse it. The default view shows three preceding exchanges. The short examples show all history when fewer exchanges exist.
- Label the three conduct criteria and submit to see confirmation. A/B submission requires a preference. Its candidate-specific conduct labels are optional.
- Navigate through the examples to inspect long wording, a short factual question, unsupported assumptions, and grounded follow-up wording. Judge predictions and following participant responses are excluded from the task display.

Submission validates the form and marks that draft submitted in memory. Reloading or closing the page clears answers. This is a display preview with no accounts, assignment service, database, or exported study records.

## Example provenance and criterion alignment

`examples.js` contains a clearly labeled illustrative extended conversation and three verbatim excerpts of `agentic_judge/examples/interview.txt`. Source speaker labels are normalized to Interviewer and Participant. Transcript excerpts retain source line numbers and turn IDs in the fixture data. Alternative candidate turns are illustrative. The provenance note identifies that on each screen.

The conduct fields use the judge's `premise`, `meaning`, and `question_burden` IDs and definitions from `agentic_judge/judging/turns.py`. Findings use `violation`, `no_observed_violation`, `unclear`, and `not_applicable`. These are a provisional subset for reviewing the interface. A/B preferences use `a`, `b`, `tie`, and `unable_to_judge`.

To adjust the sample content, edit `examples.js`. Each example supplies an ID, title, interview purpose, source note, preceding history, target interviewer turn, and alternative candidate. Include only history available before the target turn. To adjust the provisional criterion set, edit `criteria` in `app.js`.
