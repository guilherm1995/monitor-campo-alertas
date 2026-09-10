# O QUE O BOT FAZ

Três coisas, e é útil separá-las: ele **vigia** sozinho, **responde** a
comandos, e **publica** em horários fixos.

## 1. A vigia

A cada **45 segundos** (`INTERVALO_VARREDURA_COMPLETA_SEG`) o bot varre o CAMPO
inteiro e compara com o que já viu. Quando aparece coisa nova, ele avisa o
grupo -- uma vez, nunca duas.

Os três alertas individuais, cada um com contrato, nome, bairro, telefones e
técnico:

| alerta | quando dispara |
|---|---|
| **garantia** | reparo aberto num contrato que teve atendimento dentro do prazo de garantia |
| **improdutiva reincidente** | O.S. aberta num contrato que já teve improdutiva técnica na janela |
| **área de risco** | CAPEX aberto em endereço dentro do mapa de risco |

Mais o alerta de **CAPEX novo**, que é o volume do dia a dia.

E dois que ficam **desligados** até alguém pedir: **Upgrade** (UP02) e
**Mudança de cômodo** (ES15). Liga-se com `/alertas`, escolhendo a região --
ver a seção dos comandos.

O que impede a repetição são os arquivos de estado em `dados/`. Apagá-los faz
o bot reanunciar tudo o que já anunciou.

**Por que a varredura é completa e não "leve":** uma O.S. aberta e concluída
dentro de uma janela de espaçamento **nunca** seria notificada -- quando a
varredura rodasse, ela já teria saído do filtro `dataConclusao IS NULL`. Foi
exatamente o que a operação sentiu quando se tentou espaçar: "as notificações
de CAPEX diminuíram bastante".

## 2. Os comandos

Funcionam igual no Telegram e no WhatsApp, com barra. O WhatsApp ainda aceita
alguns sem barra, por herança, mas a lista mostra só a forma com barra: uma
sintaxe só para decorar.

### Onde cada um vale

O bot escuta em três lugares, e o que vale em cada um é diferente de propósito.

| onde | o que vale | para onde a resposta volta |
|---|---|---|
| **grupo de comandos** | tudo | o próprio grupo |
| **conversa privada liberada** (`BOT_PV_LIBERADOS`) | tudo, **com barra**; texto solto é pergunta para a IA | a própria conversa |
| **grupos de região** (litoral, Rio) | só `/bot` e `/carga` | o próprio grupo |

Os grupos de região são restritos porque ali está a operação inteira, incluindo
gente de fora da equipe: `/reiniciar` derruba a máquina e `/desligar` cala os
alertas. Uma pergunta errada custa uma resposta errada; um `/reiniciar` errado
custa a produção.

A conversa privada não é restrita porque a porta já é: só os JIDs em
`BOT_PV_LIBERADOS` são atendidos, e quem não está na lista é ignorado em
silêncio (o número aparece no log, que é como se descobre o JID para liberar).

**No privado, comando exige a BARRA**, e isso é regra e não detalhe: `desligar`
solto no meio de uma frase -- "vou desligar o computador" -- calaria os alertas
da operação inteira. Texto sem barra ali é sempre pergunta para a IA.

### Um despacho só

O grupo de comandos e a conversa privada passam pelo **mesmo** código de
despacho. O que muda entre os dois é uma variável: para onde a resposta volta.

Isso é deliberado. Duas listas de comandos divergiriam no primeiro comando
novo -- alguém acrescenta num lugar, esquece no outro, e o comando "some" no
privado sem erro nenhum. O `teste_comandos_pv.py` roda esse despacho contra
mensagens fabricadas e confere o destino de cada resposta, inclusive das que
uma thread manda minutos depois.

### Relatórios

| comando | o que faz |
|---|---|
| `/backlog` | backlog de todos os tipos. `/backlog capex\|reparo\|upgrade\|mudanca_comodo` para um só. **Um de cada vez**: pedido novo com um já rodando recebe "Aguarde!" em vez de entrar na fila |
| `/termometro` | termômetro de entrantes CAPEX |
| `/improdutivas` | reincidentes de improdutiva em aberto, por região |
| `/risco` | CAPEX em aberto dentro das áreas de risco do RJ |
| `/garantias` | manda a lista de garantias aos grupos regionais |
| `/carga` | prévia da carga de AMANHÃ no Litoral Norte -- balde e rotas: a capa e a lista detalhada, em duas imagens. Atualiza a base no OFS antes de contar. Sai sozinha às **15:40, 16:40 e 18:30** no grupo do litoral (a agenda de amanhã enche ao longo da tarde; uma prévia só, cedo, mostra menos do que o dia vai ter). Regras em `11-carga-do-dia-seguinte.md` |

### Consulta

| comando | o que faz |
|---|---|
| `/autenticador` | status do contrato no Autenticador. Pergunta o contrato e **espera a réplica** |
| `/bot` | a IA. Ver abaixo |

### Sistema

| comando | o que faz |
|---|---|
| `/alertas` | liga/desliga o aviso de Upgrade e Mudança de cômodo, por região |
| `/painel` | endereço do site do painel |
| `/status` | resumo do sistema e contadores do dia |
| `/comandos`, `/ajuda`, `/help` | esta lista |
| `/desligar` | pausa o monitoramento e fecha o navegador |
| `/ligar` | retoma |
| `/exibirpaineltv`, `/ocultarpaineltv` | o painel na TV |
| `/exibirnavegador`, `/ocultarnavegador` | mostrar o Chromium da varredura |
| `/reiniciar` | reinicia a máquina inteira (VPN, bot, site, painel) |

A lista é **adaptativa**: só oferece o lado que faz sentido no estado atual.
Com o painel já aberto, `/exibirpaineltv` não aparece.

#### `/alertas`, e por que ele não é sempre ligado

Upgrade e mudança de cômodo são serviço de cliente que já existe, e o volume
varia. Ligados o tempo todo em época cheia, viram ruído -- e alerta que vira
ruído estraga também os outros, porque o grupo passa a ler tudo por cima.
Então quem decide é a coordenação, e decide **por região**: ligar no RJ não
enche o litoral.

O comando pergunta onde (`1` Litoral Norte, `2` Sul RJ, `3` as duas, `0`
desligar) e **espera a réplica**, igual ao `/autenticador`. Também aceita direto:
`/alertas rj`.

Duas regras que não são detalhe:

- **Só alerta o que entrar depois de ligar.** O carimbo da hora fica no estado
  (`dados/alertas_opcionais.json`). Sem esse corte, ligar despejaria no grupo
  tudo o que já está aberto -- eram 32 upgrades e 18 mudanças de cômodo no dia
  em que o comando nasceu. Para ver o que já está aberto existe o backlog.
- **A escolha sobrevive ao reinício**, porque mora em disco. Se ficasse na
  memória, todo restart desligaria o alerta em silêncio, e silêncio é
  exatamente o que ninguém percebe.

A regra inteira vive em `alertas_opcionais.py`, e `teste_alertas_opcionais.py`
a trava.

## 3. O `/bot` -- a IA

O único comando que não devolve relatório pronto. Ele pensa em cima dos nossos
dados e conclui.

### O que acontece a cada pergunta

1. **Monta o dossiê** (`dossie_operacao.montar`) -- o retrato da operação
   agora, com todo número já apurado pelo mesmo código que gera as imagens do
   grupo. O modelo lê totais, não conta linhas.
2. **Manda a pergunta** com o dossiê e a instrução (`regras/nucleo.md`).
3. **Atende as buscas** que ele pedir, até `MAXIMO_BUSCAS` = 3.
4. **Lê a resposta** e decide se fecha ou se fica esperando réplica no grupo.

Sem pergunta, `/bot` sozinho faz o diagnóstico do momento.

Se a pergunta ficar vaga, ele devolve outra pergunta -- e aí se responde **ali
mesmo, sem `/bot`**, porque a conversa fica aberta.

### O que ele alcança, e o que não

Alcança: backlog do CAMPO, agenda do OFS, garantias, improdutivas, área de risco,
termômetro, a prévia da carga do dia seguinte, e -- pelas buscas -- o histórico
de contratos e de rotas, os dados do cliente (nome, endereço, telefone,
celular, e-mail, CEP), a situação de conexão no Autenticador com a hora da queda, e a
rede e a senha do wi-fi.

Não alcança: desenhar. O `/bot` escreve; a capa e a lista detalhada da prévia
saem pelo `/carga`, e prometer imagem seria prometer o que ele não entrega.
Também não alcança nada fora da operação de campo.

### A busca antecipada

Se a pergunta traz um número de 6 a 11 dígitos, **o código busca antes de
perguntar**. Até 9 dígitos é contrato ou O.S., e vai por `buscar_contrato`; de
10 para cima é telefone, e vai por `buscar_cliente`. O resultado chega ao modelo junto com a pergunta, marcado como
busca já feita.

Existe porque a instrução não bastava. Medido em 30/08/2026 com o
`gemini-3.5-flash-lite`: perguntado por um contrato inventado, ele respondeu
"não consta" **sem ter buscado em 3 de 6 corridas**. Endurecer o texto da
instrução não mudou nada -- 3 de 6 antes, 3 de 6 depois. Com a busca
antecipada: **6 de 6**, e mais rápido, porque ele responde numa volta só em vez
de duas.

O detalhe que faz funcionar está no aviso que acompanha o resultado: *lista
vazia aqui é uma ausência APURADA, não falta de informação*. Sem essa frase o
modelo recebe o resultado vazio e conclui que não sabe, em vez de concluir que
não existe.

Não consome as três buscas que o modelo ainda pode pedir, e custa uma leitura
de arquivo local que já está em cache.

### As quatro camadas contra a resposta inventada

1. **Instrução** -- manda buscar antes de negar, e citar a fonte de tudo.
2. **Busca antecipada** -- tira a decisão das mãos do modelo quando a pergunta
   traz um número.
3. **Avaliador** -- 16 casos em `avaliar_assistente.py` que reprovam a negação
   sem busca, a resposta fora do escopo, o chute em pergunta ambígua e a troca
   de papel por injeção.
4. **Código** -- a citação `[BUSCA]` é **removida** quando nenhuma busca foi
   feita, com aviso no log. O modelo já carimbou sem buscar, mais de uma vez.

A ordem importa: cada camada só existe porque a de cima falhou numa medição.

## 4. Os horários fixos

| o quê | quando | onde |
|---|---|---|
| painel de resultados | **min 3 de cada hora**, das **7h às 20h** | `painel_resultados.py` |
| lista de garantias | de hora em hora, das **7h às 19h** | `garantias_envio.py` |
| bases do OFS | alvo **07:40**, e de novo quando o cookie do OFS for renovado | `ofs_base_historica.py` |
| painel de abertura | das **7h às 9h** | `painel_resultados.py` |

Todos os horários são variáveis de ambiente -- os valores acima são o padrão.
