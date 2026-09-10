# ONDE ACHAR CADA COISA

Mapa de dados. Para cada informação: onde ela mora, como se chega nela, e se o
`/bot` pode vê-la.

A última coluna diz se aquele campo chega ao `/bot`. Desde 30/08/2026 os dados
do cliente chegam -- ver `06-privacidade.md`. O que ainda separa uma coluna da
outra é **onde** ela está: o dossiê carrega o que já está em memória, e a busca
alcança o resto.

## As 34 colunas do `OFS GERAL.csv`

Na ordem do arquivo. O pandas renomeia o segundo de cada par de cabeçalhos
repetidos com o sufixo `.1`.

| # | coluna | o que é | vai para o /bot? |
|---|---|---|---|
| 1 | Recurso | quem executa (técnico OU fila de praça) | **sim** |
| 2 | Data | dia da atividade, `dd/mm/aa` | **sim** |
| 3 | Status da Atividade | concluído, cancelado, não concluído, pendente, suspenso | **sim** |
| 4 | Nome | nome do cliente | **sim**, na busca |
| 5 | **Endereço** | logradouro e número | **sim**, na busca |
| 6 | Cidade | cidade da atividade | **sim** |
| 7 | Estado | UF | não |
| 8 | CEP/Código Postal | CEP | **sim**, na busca |
| 9 | **Telefone** | fixo do cliente | **sim**, na busca |
| 10 | **Telefone Celular** | celular do cliente | **sim**, na busca |
| 11 | E-mail | e-mail do cliente | **sim**, na busca |
| 12 | Intervalo de Tempo | janela contratada | não |
| 13 | Janela de Serviço | início da janela | não |
| 14 | Janela de Serviço**.1** | fim da janela (o pandas renomeia) | não |
| 15 | Data Abertura Chamado | quando o chamado nasceu | **sim** |
| 16 | Início | hora de início | **sim** |
| 17 | Fim | hora de fim | **sim** |
| 18 | Início - Fim | os dois juntos | não |
| 19 | Início do SLA | início do prazo | não |
| 20 | Fim do SLA | fim do prazo | não |
| 21 | Duração | tempo gasto | **sim** |
| 22 | Tempo de Deslocamento | tempo de rota | não |
| 23 | Tipo de Atividade | **regime -- vem tudo "Normal", é a coluna ERRADA** | não |
| 24 | Tipo de Atividade**.1** | **o tipo de verdade: Ativação, Reparo, Upgrade** | **sim** |
| 25 | Ordem de Serviço | número da O.S. | **sim** |
| 26 | Número do Cliente | id do cliente | não |
| 27 | Habilidade de Trabalho | skill exigida | não |
| 28 | Área de Trabalho | área do OFS | não |
| 29 | Chave Workzone | zona | não |
| 30 | Motivo de Encerramento das atividades | por que terminou assim | **sim** |
| 31 | Posição na Rota | ordem na rota do dia | não |
| 32 | Número do contrato | a chave que liga tudo | **sim** |
| 33 | Plano de Contrato | nome comercial do plano | **sim**, mas fora da rota |
| 34 | ID da Ordem de Serviço | id interno da O.S. | **sim** |

Duas observações que já custaram caro:

- **`Tipo de Atividade` sem sufixo é a coluna errada.** Ela vem inteira como
  `Normal`. Ler ela devolve zero garantias e nenhum erro.
- **`Plano de Contrato` sai da busca por rota**, mas fica na busca por
  contrato. Tem mais de cem caracteres por linha; numa lista de 15 atividades
  ele sozinho ocupava metade do resultado.

## Os campos do chamado do CAMPO

O chamado cru tem **46 campos**. A projeção que sobrevive à varredura
(`CAMPOS_CACHE_BACKLOG`) guarda 10:

| campo | o que é | por que está na projeção |
|---|---|---|
| `id` | id do chamado | chave |
| `fila` | código da fila (`ES02`, `ES05`...) | de onde sai a categoria |
| `enderecoUnidade` | a sigla da praça | todo agrupamento |
| `codigoContrato` | o contrato | cruzamento com OFS e Autenticador |
| `dataAbertura` | epoch em ms | idade e balde |
| `dataConclusao` | epoch em ms, ou nulo | o filtro de "aberto" |
| `agendamentoData` | `AAAA-MM-DD`, ou nulo | D0, atrasada, sem agenda |
| `nomeCliente` | nome | a regra de reincidência casa por contrato **ou por nome** |
| `enderecoBairro` | bairro | alerta individual e `/improdutivas` |
| `enderecoLogradouro` | logradouro | `/risco` responder pelo cache, com lat/lng |

`ordemServicos` -- a parte pesada -- **não** sobrevive. Ela serve para uma
coisa só, saber se a última O.S. tem pacote, e isso vira o booleano
`tem_pacote` na projeção.

### Onde acha o telefone do cliente

`extrair_telefones_do_chamado(chamado)`, e ela precisa do **chamado cru**, com
a estrutura aninhada inteira -- a projeção não serve. Ela varre os dicts de
contato procurando `numero`, `telefone`, `celular`, `fone` ou `contato`.

Custa ~1,3 s por varredura. É custo de CPU, não de memória: ela **não vaza**
(esteve acusada disso e foi inocentada na bancada).

Os telefones vão para os alertas individuais de garantia, improdutiva e área
de risco. O `/bot` também os entrega, mas por outro caminho: ele os lê da
exportação do OFS, na busca, e não desta função -- que precisaria do chamado
cru.

### Onde acha o endereço

Três lugares, e servem a coisas diferentes:

- `enderecoLogradouro` + `enderecoBairro` no cache do CAMPO -- é o que o `/risco`
  usa, com lat/lng montados em `projetar_para_cache`;
- coluna `Endereço` do OFS GERAL -- o endereço da atividade agendada;
- o mapa de área de risco (`area_risco.py`), que é polígono, não texto.

O `/bot` recebe cliente, logradouro, bairro e cidade já no dossiê. O endereço
completo com número, o CEP e os telefones vêm da busca.

### Onde acha o histórico de atendimento

**Não é no CAMPO.** O CAMPO só tem o que está aberto (`dataConclusao IS NULL`).

O histórico está na **base histórica do OFS**, e chega ao `/bot` por três
buscas, em `busca_operacao.py`:

| busca | pergunta que ela responde |
|---|---|
| `buscar_contrato(contrato)` | tudo sobre um contrato: O.S. abertas no CAMPO **e** atividades do OFS, inclusive concluídas e canceladas, de qualquer data |
| `buscar_ordem_servico(numero)` | uma O.S. específica |
| `listar_atividades_do_recurso(recurso, data)` | a rota de um técnico num dia |
| `buscar_cliente(termo, dias)` | **qualquer dado**: nome, telefone, celular, e-mail, endereço, CEP, cidade, O.S., técnico -- com janela opcional em dias |

### A busca por qualquer dado

`buscar_cliente` existe porque a pergunta do grupo quase nunca traz o número do
contrato. Traz o nome de quem ligou, ou o telefone que apareceu no visor.

Ela procura em onze colunas ao mesmo tempo e **diz em qual casou**, o que
importa: "achei 3" não diz se achou pelo nome ou pelo endereço, e são
conclusões diferentes.

Duas normalizações fazem ela acertar no caso comum:

- **acento e caixa**: quem digita escreve `marinalva farias`, a base guarda
  `MARINALVA FARIAS DE MORAES SILVA`;
- **telefone só por dígito**: `(DDD) 9XXXX-XXXX` acha `DDD9XXXXXXXX`.

Termo com menos de 4 dígitos não é tratado como número -- senão o "30" de
"30 dias" casaria com meio arquivo.

**Quando a busca fica larga** -- um primeiro nome comum casa em nome de rua e
em nome de técnico também --, ela devolve 8 linhas de amostra em vez de 40, e
diz quantas existem, quantos nomes distintos apareceram e que é para pedir o
nome inteiro. Medido: 964 linhas e 518 nomes distintos davam 28 KB de resposta;
com a amostra, 5,8 KB. Os números continuam todos lá; o que encolhe é a lista.

Quando não acha nada, o resultado diz com todas as letras que a ausência é
**apurada** -- procurou nas onze colunas, em todo o histórico, e não existe.
Sem essa frase o modelo conclui que não sabe, em vez de concluir que não
existe.

Cada uma devolve, além das linhas, as **contagens já apuradas** --
`contagem_por_status`, `contagem_por_tipo`, `total_produtivas` -- para o modelo
não contar linhas. Teto de `TETO_LINHAS_BUSCA` = 40 linhas por busca.

### Onde acha se o cliente está online

Autenticador, e só ele. Ver `07-como-consultar.md`. Não há cópia local: é sempre
consulta ao vivo, e sempre por dentro da VPN.

### Onde acha o que já foi avisado no grupo

Arquivos de estado do próprio bot, na pasta `dados/`: O.S. já notificadas,
agendamentos já conhecidos, reparos já confirmados como garantia. É o que
impede o mesmo alerta de sair duas vezes.
