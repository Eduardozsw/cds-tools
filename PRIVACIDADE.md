# Privacidade e segurança

> **Projeto não oficial.** Não tem vínculo com a Unipê, a Cruzeiro do Sul Educacional ou a
> Blackboard, e não é endossado nem mantido por elas. É um projeto pessoal, de código aberto,
> feito por um estudante. Ao usar, você segue as regras que valem para a sua própria conta.

## Onde ficam os dados

Tudo fica no seu computador. Não há servidor, conta, telemetria nem analytics.

| O quê | Onde |
|---|---|
| Sessão do Blackboard (cookies) | `~/.cds-tools/state.json` |
| Perfil do navegador usado no login | `~/.cds-tools/profile/` |
| Cópia local de avisos, prazos, notas e lista de conteúdo | `~/.cds-tools/blackboard.db` |
| Registro do fluxo de login (sem cookies nem query strings) | `~/.cds-tools/login-trace.log` |
| Material baixado e resumos | `~/faculdade/` |

Instalações antigas usam `~/.blackboard-mcp/` no lugar de `~/.cds-tools/`.

## O que nunca é guardado

- **Sua senha.** O login acontece no navegador, na página da Microsoft da instituição. O
  programa só vê os cookies que o Blackboard devolve depois.
- **Avaliações.** Questões, tentativas e envios não são lidos nem baixados.

## O que o programa pode fazer na sua conta

Somente **ler** material, avisos, notas e calendário. Isso não depende das instruções dadas
ao agente de IA: o cliente HTTP só aceita uma lista fixa de endereços da API
(`ALLOWED_PATHS` em `src/cds_tools/client.py`) e recusa qualquer outro antes de sair para a
rede. Os testes (`tests/test_client.py`) conferem que endereços de avaliação são recusados.

Ele nunca envia nada: não entrega atividade, não posta em fórum, não altera nada.

## Cuidado com os cookies

`state.json` dá acesso à sua conta enquanto a sessão for válida, como se alguém estivesse
com seu navegador aberto. Não compartilhe a pasta `~/.cds-tools`, não a coloque em backup
público nem anexe arquivos dela em issues. O `login-trace.log` é seguro para anexar.

## Apagar tudo

Apague `~/.cds-tools` (ou `~/.blackboard-mcp`). Para remover o programa:
`uv tool uninstall cds-tools`. A pasta `~/faculdade` é sua e não é apagada.

## Uso responsável

- O `watch` tem intervalo mínimo de 30 minutos; o recomendado é a cada 2 horas.
- Os endpoints usados não são documentados para alunos e podem mudar sem aviso.
- A ferramenta é para organização e estudo. Não use para responder avaliações.
