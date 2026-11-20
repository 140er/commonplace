### Deploy signal fixture inheritance

The deploy signal tests launch Bash in a private session with SIGINT reset to its default disposition before exec. A noninteractive runner can inherit ignored SIGINT; Bash preserves that state and cannot install the script's INT trap. The fake reference holder sends the signal after the script installs its traps, so no extra readiness wait is needed. Both parent dispositions must retain HUP=129, INT=130, TERM=143, reference cleanup and the refusal to reach Docker. This fixes test launch isolation; the production deploy script is unchanged.
