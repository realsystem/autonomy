# Home Assistant add-ons

`autonomy-station/` is the Field Station cell, step 1 (M10, read only). See
[design/field-station-reference-deployment.md](../../design/field-station-reference-deployment.md) §4.

Install on a Home Assistant OS box (after a full backup, off site): copy `autonomy-station/` into the
`/addons/` share (or add this repository as an add-on repository), install **Autonomy Field Station**,
set the `allow` list to the entities it may read, start it. Roll back by stopping and uninstalling it.
It does not touch `configuration.yaml`, packages or automations, and it never restarts Home Assistant.

It records to `/data/station-events.db`; read it with `autonomy station events --db <file>`.
The same code runs in the lab (`ops/lab/lab.sh up --farm`) against a Home Assistant stand-in.
