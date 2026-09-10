# 11 -- A prévia da carga do dia seguinte

Regras ditadas pela operação em 31/08/2026. Implementadas em
`carga_litoral.py` (a conta), `carga_render.py` (as imagens) e
`atualizar_bases.py` (a base fresca).

---

## O que é

Todo fim de tarde a coordenação precisa saber o que cai no colo dos técnicos do
litoral no dia seguinte: quantas atividades, de que tipo, em que cidade, de
manhã ou de tarde. Isso sempre foi feito à mão, no Excel: uma tabela dinâmica
sobre a extração do OFS -- a **capa** -- e a **lista detalhada** logo abaixo.

Duas saídas, e as duas saem da mesma conta:

| Comando | O que devolve |
|---|---|
| `/bot ...` | **escreve**: os totais em texto, no grupo |
| `/carga` | **desenha**: duas imagens, a capa e a lista detalhada |

E ela sai sozinha, **três vezes por dia, no grupo do litoral**:

| horário | por quê |
|---|---|
| **15:40** | quando a coordenação começa a montar a rota do dia seguinte |
| **16:40** | pega o que entrou na última hora |
| **18:30** | o retrato de fechamento do dia |

São três porque a agenda de amanhã enche ao longo da tarde: uma prévia só, cedo,
mostra menos do que o dia vai ter. Os horários vivem em
`CARGA_AUTOMATICA_HORARIOS`, em `HH:MM` separados por vírgula, e o
`teste_agendador_carga.py` trava o cálculo -- inclusive a virada do dia, que é
onde é fácil pular 24 horas sem querer.

O **primeiro** envio do dia leva um rodapé que o `/carga` pedido à mão não leva
-- e só o primeiro: a instrução é útil uma vez, repetida três vezes por dia vira
paisagem, e paisagem ninguém lê.

    ℹ️ A carga é gerada automaticamente. Basta pedir "/carga" aqui no grupo
       para ter uma prévia atualizada.

Quem digitou `/carga` já sabe que o comando existe. Quem recebe a prévia sem ter
pedido é que precisa saber que pode pedir outra, mais tarde, em vez de reler uma
tabela de três horas atrás. O rodapé vai na SEGUNDA imagem: na primeira, ficaria
por cima da tabela que a pessoa abriu para ler.

Elas nunca devem divergir. No dia em que divergirem, quem conferir a imagem
contra o texto não vai saber qual das duas está certa -- e por isso as duas
chamam `carga_litoral.levantar_carga`, e nenhuma refaz a conta por conta
própria.

---

## O recorte, regra por regra

### 1. A carga inteira: o balde E as rotas

A fonte é o OFS, e entra **tudo o que o dia tem**: o que ainda está no balde da
localidade esperando alguém pegar, e o que já está na rota de um técnico.

Isto mudou em 01/09/2026. A primeira versão contava só o balde, com o argumento
de que o que já tem dono não é carga a distribuir. A operação corrigiu: a
prévia serve para saber **quanto trabalho o dia tem**, e metade do trabalho já
estar distribuído não o faz sumir.

**A divisão entre balde e rota não aparece na prévia**, e isso também é regra.
Ela nunca foi o assunto: existe só para o total não divergir da planilha.
Mostrá-la na capa pediria atenção para uma decisão que ninguém toma olhando a
prévia -- e cada elemento a mais numa tabela lida no celular custa a atenção
que deveria ir para o número que importa.

Como se reconhece o balde: balde e técnico ocupam a mesma coluna do OFS,
`Recurso`. O que diferencia é que o nome do balde é o da localidade. Os baldes
do litoral estão em `BALDES_LITORAL`:

    CARAGUATATUBA · SAO SEBASTIAO · BOICUCANGA · ILHABELA

A lista é explícita de propósito. Adivinhar "isto parece nome de lugar, aquilo
parece nome de gente" erraria no dia em que entrasse um técnico chamado
Bertioga. Conferido contra a base em 31/08/2026: estes quatro nomes nunca
aparecem com `Posição na Rota` maior que zero, e nenhum técnico aparece com
zero e nome de cidade.

**A lista não é mais um filtro** -- ela só marca cada linha com `no_balde`. A
marca não vai para a tela: ela existe para o teste conferir os números da
planilha do Excel, que contava só o balde, contra o subconjunto certo.

#### A mesma O.S. duas vezes

Uma O.S. repassada de um técnico para outro pode aparecer duas vezes no mesmo
dia. Enquanto o recorte era só o balde isso não acontecia; contando as rotas,
passa a poder. A prévia guarda os `ID da Ordem de Serviço` já vistos e conta
cada uma **uma vez**.

Medido em 01/09/2026: zero repetidas naquele dia. O seguro fica porque contar
duas vezes inflaria a carga sem ninguém notar.

### 2. Status: tudo menos cancelado

Entra qualquer status -- pendente, suspenso, não concluído, o que for --
**menos cancelado**.

A regra está escrita no código como o que fica de FORA, e não como uma lista do
que entra. Foi assim que ela foi dita, e uma lista do que entra deixaria um
status novo do OFS silenciosamente fora da conta.

### 3. Tipo: só os três de campo

    Ativação · Mudança de Endereço · Reparo

Upgrade/Downgrade, Mudança de Cômodo e Clean Up ficam de fora.

A comparação é por prefixo, sobre o texto achatado: "Reparo" pega "Reparo
Corretivo" e qualquer outro reparo que o OFS venha a nomear.

A coluna do tipo é **`Tipo de Atividade.1`**, a com sufixo. A sem sufixo é toda
"Normal" na base inteira -- ler a errada devolveria uma prévia vazia sem
nenhum erro. É a mesma armadilha da base de garantias (ver `05-armadilhas.md`).

### 4. São Sebastião se parte em três

São Sebastião não conta como uma cidade só, **e a divisa da cidade não é a
divisa das rotas**. Um endereço de São Sebastião pode cair em três lugares
diferentes da capa:

- **CARAGUATATUBA** -- a ponta norte, encostada na divisa. Quem atende sai de
  Caraguatatuba, não do topo nem da costa: `Canto do Mar`, `Jaraguá` e
  `Enseada`. A lista (`BAIRROS_DE_CARAGUATATUBA`) leva também os bairros que
  **são de Caraguatatuba** e chegam com o campo Cidade do OFS dizendo São
  Sebastião -- `Pegorelly`, `Casa Branca` e `Indaiá` --, e duas grafias tortas
  do Canto do Mar que o OFS escreve: `CANTO DO MA` e `CANTO O MAR`;
- **TOPO** -- os bairros da lista abaixo;
- **COSTA SUL** -- todo o resto da cidade.

A ordem da conferência é regra: primeiro a ponta norte, depois o topo, e o
resto cai em Costa Sul. As duas listas não se cruzam hoje, mas a ordem está
escrita para o dia em que se cruzarem -- e aí o certo é a atividade ir para a
rota que a atende de verdade.

As listas fechadas são as duas primeiras; o resto é Costa Sul por construção.
Isso importa: bairro que ninguém previu cai em COSTA SUL automaticamente, que é
o comportamento certo, em vez de sumir da visão.

Esta regra nasceu da primeira prévia rodada sobre dado real, em 31/08/2026: as
atividades de Canto do Mar e Jaraguá saíram classificadas como Costa Sul, e a
operação corrigiu na hora. Sem a correção, a capa teria mandado para a rota da
costa sul três serviços que ficam do outro lado da cidade. A **Enseada** entrou
na lista em 01/09/2026, pela mesma razão e pela mesma rota.

A lista tende a crescer assim, um bairro por vez, conforme a prévia real mostra
um endereço no lugar errado. Acrescentar é só somar o nome achatado (maiúsculas,
sem acento) em `BAIRROS_DE_CARAGUATATUBA` e pôr o caso no `teste_carga.py`.

### Varrer a base é mais rápido que esperar a prévia errar

Em 03/09/2026, em vez de esperar mais um bairro aparecer torto, a base inteira
foi varrida: todos os endereços com Cidade = São Sebastião, agrupados por
bairro, com a classificação que o código dá hoje. Saíram 60 bairros distintos, e
com eles quatro achados que a prévia levaria semanas para mostrar:

- `CANTO DO MA` e `CANTO O MAR` -- o mesmo Canto do Mar, escrito errado pelo
  OFS. Cinco atividades iam para a Costa Sul;
- `PEGORELLY` e `JARDIM CASA BRANCA` -- bairros de Caraguatatuba com o campo
  Cidade errado. O primeiro denuncia a si mesmo: o logradouro é
  "RUA DEZ RESIDENCIAL 1 NOVA CARAGUA";
- `PORTAL DA OLARIA` (11 atividades) já caía em TOPO, mas **por acaso**: é
  "Pontal da Olaria" escrito errado, e bateu na entrada solta `OLARIA`;
- `MORRO O ABRIGO`, que estava na lista "por segurança", não aparece nenhuma
  vez -- só `MORRO DO ABRIGO`, 16 vezes. Foi removido.

Vale repetir essa varredura quando a operação desconfiar de um número: ela custa
segundos e mostra o universo inteiro, em vez de um caso por dia.

Bairros do TOPO (`TOPO_BAIRROS`):

    Topolândia · Centro · São Francisco · Itatinga · Morro do Abrigo ·
    Vila Amélia · Pontal da Olaria · Olaria · Cigarras · Praia das Cigarras ·
    Praia da Olaria · Praia do Arrastão · Praia Deserta · Vila Indústria ·
    Varadouro · Praia Grande · Porto Grande · Pontal da Cruz

**Praia Grande e Porto Grande são bairros diferentes**, e os dois são TOPO. Um
nunca pega o outro porque o casamento é por frase inteira -- mas os dois estão
lado a lado na lista de propósito, para quem passar por aqui não achar que é
duplicata e apagar um.

### 5. O que NÃO atendemos em São Sebastião

Até 03/09/2026 a cidade inteira era nossa: todo endereço dela caía em TOPO,
COSTA SUL ou CARAGUATATUBA. Agora existe uma saída, em
`BAIRROS_QUE_NAO_ATENDEMOS` -- hoje só a **Vila Itaguá**.

Repare o **sentido** das duas listas de exclusão, porque ele é oposto de
propósito:

| cidade | a lista diz | e o resto |
|---|---|---|
| Bertioga | o que **entra** | sai da prévia |
| São Sebastião | o que **sai** | entra na prévia |

Cada uma foi escrita no sentido que deixa o caso comum ser o padrão: quase toda
Bertioga não é nossa, e quase toda São Sebastião é. Escrever as duas do mesmo
jeito obrigaria a manter uma lista enorme de um dos lados.

#### Confirmados como COSTA SUL

Em 03/09/2026 a operação passou um a um pelos bairros que a varredura mostrou:

    Barequeçaba · Pitangueiras · Piavu · Santiago · Calhetas ·
    Engenho (ou Praia do Engenho)

Eles já caíam em Costa Sul pelo padrão -- estão no `teste_carga.py` para
ninguém "consertar" depois achando que faltavam na lista do topo.

**O Engenho de São Sebastião não é o Engenho de Ilhabela.** São dois bairros com
o mesmo nome em cidades diferentes, e o que os separa é a cidade: a regra de
bairro de São Sebastião nem chega a rodar para um endereço de Ilhabela. Há um
caso no teste guardando isso.

#### Indaiá: depende da cidade com que chega

É o único bairro cuja resposta muda conforme o campo Cidade:

| Cidade no OFS | resultado |
|---|---|
| BERTIOGA | **fora da prévia** -- não é nosso |
| CARAGUATATUBA | CARAGUATATUBA |
| SÃO SEBASTIÃO | CARAGUATATUBA -- o bairro é de lá, o campo Cidade é que veio errado |

### 6. Bertioga entra por bairro, e às vezes não entra

Bertioga é o único caso em que a resposta certa pode ser **"esta linha não é
nossa"**. Nas outras cidades o que se decide é para ONDE a atividade vai; aqui
se decide antes SE ela entra.

Cidade `BERTIOGA` no OFS chega por dois motivos diferentes, e só um é nosso:

- **Costa Sul mal classificada.** Boiçucanga, Maresias, Juquehy, Barra do Sahy,
  Camburi, Baleia, Paúba, Boracéia e companhia são São Sebastião no mapa e
  Costa Sul na rota, mas saem com cidade Bertioga. **Perder essas é perder
  carga que é nossa** -- e é para não perdê-las que esta regra existe;
- **Bertioga de verdade, em bairro que não atendemos.** São João foi o caso que
  apareceu na primeira prévia real, em 31/08/2026, contado como Costa Sul.
  Contar essas é mandar rota para serviço que não é nosso.

A regra, então: bairro que está em `BAIRROS_DE_BERTIOGA_QUE_ATENDEMOS` entra
como **COSTA SUL**; bairro fora dela **sai da prévia**.

Não existe linha "BERTIOGA" na capa. Bertioga em balde de outra empresa nunca
chega até aqui, porque o filtro do balde já a descarta.

#### A lista mora num lugar só

`BAIRROS_DE_BERTIOGA_QUE_ATENDEMOS` vive em `carga_litoral.py`, e o
`bot_campo_monitoramento.py` a importa de lá -- é a mesma lista que já restringia
as siglas **BERT** e **BERTN** no monitoramento. Antes eram duas cópias iguais
em dois arquivos.

Duas listas divergem na primeira vez que um bairro entra numa e não na outra, e
a divergência sai calada dos dois lados: o monitoramento deixaria de alertar
sobre um serviço que a prévia manda atender, ou o contrário. O teste confere
que as duas são o mesmo objeto, e não só listas de conteúdo igual.

Caraguatatuba e Ilhabela não se partem -- elas nunca aparecem divididas na
capa. Caraguatatuba, aliás, só RECEBE: além do que é dela, ela fica com a ponta
norte de São Sebastião.

#### Por que a comparação de bairro é código, e não um filtro

O nome do bairro vem no endereço do OFS, que é texto livre digitado por gente.
O mesmo lugar aparece como `CIGARRAS`, `Praia das Cigarras` e
`BALNEARIO CIGARRAS`; São Francisco vem com e sem acento; Topolândia vem
`TOPOLANDIA`.

Comparar o texto cru classificaria a maioria como COSTA SUL **sem emitir aviso
nenhum** -- o pior tipo de erro, o que sai calado e com cara de resposta.

Então: o bairro é extraído do penúltimo pedaço do endereço (`<logradouro>,
<número> <bairro>, <cidade> - <UF>`), o número da casa é removido, e a
comparação acontece sobre o texto achatado -- maiúsculas, sem acento, espaços
colapsados -- procurando a **frase inteira** entre limites de palavra.

---

## A base é atualizada ANTES de responder

Regra geral, e vale para três coisas: **rota de técnico**, **backlog** e
**prévia da carga**.

O `OFS GERAL.csv` é refeito de hora em hora por um serviço à parte. Isso basta
para painel e não basta para pergunta: uma resposta montada sobre um arquivo de
50 minutos atrás está errada de um jeito particularmente ruim -- ela é
**plausível**. Quem lê não tem como desconfiar, porque o número tem a cara de
um número certo.

Como funciona (`atualizar_bases.garantir_ofs_fresco`):

- se o arquivo tem menos de 15 minutos, ele já é fresco e nada acontece. Três
  perguntas seguidas não custam três downloads;
- senão, a janela do OFS é refeita -- 72 pedidos, ~8 segundos;
- uma atualização de cada vez no processo inteiro. Duas perguntas simultâneas
  não gravam o mesmo arquivo ao mesmo tempo;
- se falhar (sessão do OFS vencida é rotina), a pergunta **não morre**: a
  resposta sai com a hora da última carga, e a próxima tentativa só acontece
  cinco minutos depois -- senão cada pergunta pagaria o timeout do OFS.

O resultado das buscas traz um campo `base_de` dizendo de quando é a base. Ele
não é decorativo: quando a atualização falha, essa frase tem de entrar na
resposta.

---

## O que a capa mostra

Linhas: cidade, e sob ela um recuo por tipo de atividade.
Colunas: os turnos que existirem naquele dia, mais `Total Geral`.

As colunas de turno são dinâmicas de propósito. Na maioria dos dias são Manhã e
Tarde, e a capa fica igual à do Excel; mas o OFS também usa "Início Manhã" e
"Almoço", e uma coluna fixa faria essas atividades sumirem da contagem sem
avisar. A ordem preferida é Manhã, Início Manhã, Almoço, Tarde; um turno novo
entra depois desses, nunca desaparece.

Todos os totais são calculados no código -- por turno, por tipo, por cidade e o
geral. Nenhum é deixado "para quem lê somar depois". É a mesma regra do dossiê,
e pela mesma razão: **contar é de graça aqui e caro lá**.

---

## Conferência

O ensaio que trava essas regras reproduz a planilha real de um dia e confere a
capa inteira contra os números que a operação apurou à mão:

    CARAGUATATUBA  18 manhã / 5 tarde / 23
    COSTA SUL       3 manhã / 5 tarde /  8
    ILHABELA        2 manhã / 1 tarde /  3
    TOTAL          23 manhã / 11 tarde / 34

Ele também mete ruído na base -- um cancelado, um Upgrade, uma atividade já
roteirizada para técnico, uma de Três Rios e uma de outro dia -- e exige que
nenhum deles apareça. Ao mexer em qualquer regra deste arquivo, rode o ensaio
antes de instalar.
