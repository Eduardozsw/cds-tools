# cds-tools

> [!IMPORTANT]
> Projeto pessoal e **não oficial**, sem vínculo com a Unipê, a Cruzeiro do Sul Educacional
> ou a Blackboard. Somente leitura: nunca abre avaliações nem envia nada. Use com
> moderação e segundo as regras da sua conta. Veja [Privacidade e segurança](PRIVACIDADE.md).

CLI e servidor MCP para o Blackboard Ultra da Cruzeiro do Sul / Unipê
(`bb.cruzeirodosulvirtual.com.br`). Seu agente de IA (Claude Code, Claude Desktop, Cursor,
Codex...) passa a consultar avisos, prazos, notas e material das suas disciplinas, baixar o
material para uma pasta de estudos e escrever resumos.

Nenhuma senha é armazenada: o login é feito no navegador, pela conta Microsoft da instituição.

## Instalação

**1.** Instale o [uv](https://docs.astral.sh/uv/) (uma vez só):

```powershell
# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```
```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**2.** Instale o cds-tools:

```bash
uv tool install git+https://github.com/Eduardozsw/cds-tools
```

**3.** Rode o assistente:

```bash
cds-tools init
```

Ele abre o navegador para o login, sincroniza suas disciplinas, baixa o material para
`~/faculdade` e registra o MCP no Claude Code (se ele estiver instalado). Também mostra o
bloco de configuração para outros agentes.

Para atualizar: `uv tool upgrade cds-tools`.

O login usa o Edge instalado. Sem Edge (comum no Linux), rode `cds-tools install-browser` e
defina `BB_BROWSER_CHANNEL=""`.

## Outros agentes

Qualquer cliente MCP com servidores locais (stdio) funciona. A configuração não depende de
pasta nenhuma:

```json
{
  "mcpServers": {
    "cds": { "command": "cds-tools" }
  }
}
```

Claude Code: `claude mcp add --scope user cds -- cds-tools`.

Agentes só na web (ChatGPT, claude.ai) não conseguem rodar servidores locais.

## Tools

| Tool | O que faz |
|---|---|
| `cds_login` | Abre o navegador para (re)login |
| `cds_whoami` | Usuário logado |
| `cds_list_courses` | Disciplinas, das mais recentes para as mais antigas |
| `cds_list_contents` | Conteúdo da disciplina ou de uma pasta |
| `cds_get_content` | Texto, anexos e filhos de um item |
| `cds_download_attachment` | Baixa um anexo para `~/.cds-tools/downloads` |
| `cds_upcoming_deadlines` | Calendário dos próximos N dias |
| `cds_get_grades` | Notas da disciplina |
| `cds_list_announcements` | Avisos da disciplina |
| `cds_parse_url` | Extrai IDs de uma URL do Ultra |
| `cds_sync_data` | Atualiza o banco local |
| `cds_whats_new` | O que mudou desde a última consulta |
| `cds_export_ics` | Prazos como calendário `.ics` |
| `cds_sync_materials` | Baixa o material para a pasta de estudos |
| `cds_list_materials` | Materiais e resumos da pasta de estudos |
| `cds_read_material` | Texto de um material (PDF, .md, .txt) |
| `cds_save_summary` | Grava um resumo em `<período>/<matéria>/resumos` |

Pasta de estudos: `~/faculdade/<período>/<matéria>/{materiais,resumos}`.

## CLI

```bash
cds-tools init                       # login + sync + estado inicial + material + MCP
cds-tools login                      # refazer o login
cds-tools sync [--materials]         # atualiza o banco local
cds-tools deadlines [--course ED]    # prazos futuros (offline)
cds-tools grades [--course ED]       # notas (offline)
cds-tools news --unread --mark-seen
cds-tools ics --out prazos.ics       # prazos para Google Agenda / Outlook / celular
cds-tools watch --once --notify      # o que mudou desde a última vez
cds-tools watch --once --jsonl --fail-on-error   # para scripts
```

Sem comando (ou com `serve`), sobe o servidor MCP.

`watch` emite eventos `news`, `deadline`, `grade`, `material` (status `new`/`changed`) e `error`.
Cada item é lembrado por id + impressão digital dos campos relevantes, então nada se repete.
Disciplinas que respondem 403 para alunos aparecem em `restricted` e não contam como falha.
No modo contínuo, o intervalo padrão é 2 h e o mínimo é 30 min.

Agendar no Windows (a cada 2 h, com notificação):

```
schtasks /Create /SC HOURLY /MO 2 /TN "CDS Tools watch" /TR "cds-tools watch --once --notify"
```

macOS/Linux (cron): `0 */2 * * * $HOME/.local/bin/cds-tools watch --once --jsonl --fail-on-error`

## Como funciona

1. `login` abre o navegador com um perfil dedicado (`~/.cds-tools/profile`) direto na
   entrada SAML do Blackboard (`/auth-saml/saml/login`), que autentica pela conta Microsoft
   institucional.
2. Quando `/learn/api/public/v1/users/me` responde 200 (consultado de dentro da aba, com URL
   absoluta, porque o Ultra usa `<base>` apontando pro CDN), os cookies são exportados para
   `~/.cds-tools/state.json`. A navegação do login fica em `~/.cds-tools/login-trace.log`
   (sem valores de cookie nem query strings).
3. As tools usam esses cookies via `httpx` nas APIs REST que o próprio Ultra consome, e só
   nos caminhos de `ALLOWED_PATHS` (`src/cds_tools/client.py`).
4. Se a sessão expirar, o cliente tenta renová-la em modo headless (o SSO costuma lembrar do
   login). Se não conseguir, pede um novo `login`.

## Variáveis de ambiente

`BB_BASE_URL`, `BB_DATA_DIR`, `BB_DB`, `BB_DOWNLOAD_DIR`, `BB_STUDY_DIR`, `BB_BROWSER_CHANNEL`,
`BB_LOGIN_TIMEOUT`.

## Desenvolvimento

```bash
git clone https://github.com/Eduardozsw/cds-tools
cd cds-tools
uv sync
uv run pytest           # offline, com respostas da API em tests/fixtures
uv run ruff check .
```

Fixtures novas devem ser respostas reais **anonimizadas**: sem nomes, e-mails, matrículas ou
IDs de usuário. Se a API mudar de formato, o cliente deve dar erro, nunca devolver lista vazia.

Inspirado no [sigaa-tools](https://github.com/PucaVaz/sigaa-tools).

## Licença

MIT. Veja [LICENSE](LICENSE).
