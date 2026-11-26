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
- **Avaliações.** Tentativas nunca são iniciadas. O enunciado e os anexos só são lidos de uma
  tentativa que você já abriu no navegador.

## O que o programa pode fazer na sua conta

Somente **ler** material, avisos, notas, calendário e o enunciado de avaliações que você já
abriu. Isso não depende das instruções dadas ao agente de IA: o cliente HTTP só faz GET e só
aceita uma lista fixa de endereços da API (`ALLOWED_PATHS` em `src/cds_tools/client.py`),
recusando qualquer outro antes de sair para a rede. Os testes (`tests/test_client.py`)
conferem que os demais endereços de avaliação são recusados.

A única exceção é `cds_submit_assessment`, que entrega um arquivo como resposta de uma
avaliação. Ela não passa pelo cliente HTTP: abre o navegador e faz o que você faria à mão,
só numa tentativa que você já abriu, e só envia com `confirm=True`. Sem isso, apenas mostra o
que seria enviado. O envio é irreversível e consome a tentativa. Fora isso, o programa não
posta em fórum nem altera nada.

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
