# MAPA DO CÓDIGO

Onde mexer para mudar cada coisa. São dois projetos na mesma máquina, e eles
conversam por disco e por uma ponte HTTP em localhost.

```
migracao_linux/
├── bot/     o processo que vigia o CAMPO e fala nos grupos   (~15.000 linhas)
└── site/    o painel web que a operação abre no navegador  (~13.000 linhas)
```

## O bot

### O centro

`bot_campo_monitoramento.py` (8.943 linhas) -- o processo. Login no CAMPO, captura
de token, varredura, alertas, comandos do Telegram e do WhatsApp, painel de TV,
ponte HTTP para o site. É grande porque é o único que tem estado: a lista de
chamados em memória e a sessão dos dois mensageiros.

`index.js` -- o serviço do WhatsApp (Baileys). Recebe e manda mensagem; o
Python fala com ele por HTTP em localhost.

### Os motores de cálculo

| arquivo | responsabilidade |
|---|---|
| `backlog_capex.py` | Ativação e Mudança de endereço. **Aqui moram as regiões, os códigos de fila e os prazos de CAPEX** |
| `backlog_reparo.py` | Reparo, Upgrade, Mudança de cômodo, e os prazos deles |
| `backlog_ofs.py` | cruzamento com o `OFS GERAL.csv` -- é o "Enviado D0" |
| `backlog_conveniencia.py` | cruzamento com a planilha de conveniência |
| `backlog_envio.py` | junta tudo e manda para os grupos |
| `backlog_render.py` | as imagens de backlog |
| `improdutivas.py` | a base de improdutivas e os dicionários de motivo |
| `area_risco.py` | os polígonos do mapa de risco e a lista de ruas |
| `garantias_lista.py` | os dados da garantia. **Aqui mora a lista maior de siglas** |
| `garantias_render.py` / `garantias_envio.py` | a imagem e o envio |
| `termometro_render.py` | entrantes contra a média |
| `painel_resultados.py` | o painel de hora em hora |
| `ofs_extracao.py` | baixa a exportação do OFS por cookies |
| `ofs_base_historica.py` | acumula as exportações; é o que as buscas leem |
| `vpn_sempre_ativa.py` | mantém o túnel de pé |

### O `/bot`

| arquivo | responsabilidade |
|---|---|
| `regras/` | **este diretório.** As regras e a instrução |
| `dossie_operacao.py` | monta o retrato em texto. Todo número já apurado |
| `busca_operacao.py` | as três buscas, e a lista branca de colunas |
| `assistente_ia.py` | fala com o motor, atende as buscas, lê a resposta |
| `avaliar_assistente.py` | os 14 casos de avaliação |
| `eval/` | o dossiê congelado e o OFS de mentira |

### Regra de ouro deste conjunto

**Prazo, sigla e código de fila moram nos `backlog_*.py`.** O dossiê e as
regras derivam deles. Escrever o valor num segundo lugar já produziu o defeito
de 30/08/2026, em que o glossário dizia 5 dias e o código usava 7.

## O site

Flask. A operação entra por e-mail, com senha própria ou conta Google.

| rota | o que é |
|---|---|
| `/painel` | as 4 telas do painel, e gerar novas a partir de um upload |
| `/backlog` | lê o que o bot produziu, e pede backlog novo pela ponte |
| `/garantias` | a **mesma** lista que o bot manda aos grupos |
| `/confirmacao` | cruza a agenda de hoje com as respostas do formulário |
| `/alertas/garantias` | avisa na tela quem está com o site aberto |
| `/entrar`, `/usuarios` | a portaria e o cadastro |
| `/saude` | o serviço está de pé? |

| arquivo | responsabilidade |
|---|---|
| `web/app.py` | as rotas |
| `web/acesso.py` | quem entra, por onde, e o que fazer com quem insiste errado |
| `web/fontes/planilhas.py` | registro único das planilhas: onde estão, como ler, como aceitar envio |
| `web/fontes/confirmacao.py` | a regra de confirmação de agenda |
| `web/fontes/backlog.py` | **só lê** a pasta do bot, nunca escreve |
| `web/fontes/ponte_bot.py` | HTTP para o processo do bot, porta **3940**, só localhost |
| `operacional/analitico.py` | recria em código as ~50 colunas de fórmula da aba ANALITICO |

## Como os dois conversam

**Por disco**, para dado: o bot escreve em `bot/dados/`, o site lê. O site
nunca escreve na pasta do bot.

**Pela ponte HTTP**, para ação: quando o site precisa que o **processo** do bot
faça algo -- gerar backlog novo, mandar não confirmados no grupo --, ele chama
`localhost:3940`. Tem de ser assim porque o estado (lista de chamados, sessão
do WhatsApp) mora no processo, e o site não o tem.

## O que roda como serviço

| serviço | o que é |
|---|---|
| `campo-bot` | o processo do bot |
| `campo-vpn` | o túnel |
| `9router` | o motor de IA, em `127.0.0.1:20128` |
| o site | Flask |

A chave do motor de IA fica em
`/etc/systemd/system/campo-bot.service.d/ia.conf`, modo 600 -- nunca no código.
