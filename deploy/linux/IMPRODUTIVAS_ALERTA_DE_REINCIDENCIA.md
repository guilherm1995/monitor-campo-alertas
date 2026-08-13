# Alerta de reincidência: entrante que já foi improdutiva

> Criado em **13/08/2026**, quando o `/improdutivas` foi aposentado e a mesma
> classificação passou a rodar sozinha a cada entrante.

---

## 1. O que o bot faz

Ele pergunta, em **dois** momentos:

> este contrato — ou este nome — já teve uma improdutiva nos últimos 30 dias?

| Quando | Título da mensagem |
|---|---|
| Uma O.S. de CAPEX **entra** | `IMPRODUTIVA ANTERIOR` |
| Uma O.S. já conhecida ganha **agendamento novo** | `REMARCADA APÓS IMPRODUTIVA` |

O segundo caso entrou em **13/08/2026**, por correção da operação. Só o
entrante não bastava: o técnico vai, volta improdutivo, remarcam — e a
remarcação **é** a reincidência, mas passava calada, porque a O.S. já estava
em `os_notificadas` e o ramo do entrante nunca mais roda para ela. O bot só
enxergava o primeiro round de cada O.S.

Que a mesma O.S. volta é visível na própria base: no arquivo de 13/08 a O.S.
`12180211` aparece duas vezes, uma `cancelado` e outra `suspenso`.

### O aviso do entrante

```
IMPRODUTIVA ANTERIOR: CGT
• Contrato: 908088
• Cliente: FULANO DE TAL
• Bairro: BALNEARIO DOS GOLFINHOS
• Telefone(s): 12997680556
• Anterior: Problema de infraestrutura em 25/07 (há 19 dias)
• Casou por: contrato
• Técnico anterior: MARCOS VINICIUS PIRES
• OS anterior: 12250989
```

As quatro primeiras linhas repetem a mensagem de CAPEX de propósito: no
grupo as duas se separam quando alguém escreve no meio, e a segunda precisa
se sustentar sozinha.

Quando o casamento vem pelo **nome**, a linha muda para
`Casou por: NOME (confira — pode ser homônimo)`. Contrato é chave exata;
nome não é, e quem lê precisa saber quanto confiar antes de agir.

A linha `Improdutivas na janela: N` só aparece quando há mais de uma.

### O aviso da remarcação

Mesma mensagem, com o título trocado e uma linha a mais:

```
REMARCADA APÓS IMPRODUTIVA: CGT
• Contrato: 908088
• Cliente: FULANO DE TAL
• Bairro: BALNEARIO DOS GOLFINHOS
• Telefone(s): 12997680556
• Novo agendamento: 20/08
• Anterior: Problema de infraestrutura em 25/07 (há 26 dias)
• Casou por: contrato
```

O título separa os dois de propósito: "entrou uma O.S. de quem já deu
improdutiva" e "remarcaram a O.S. que deu improdutiva" pedem reações
diferentes, e quem lê no grupo decide pela primeira linha.

Aqui a janela de 30 dias conta a partir da **data nova**, não da abertura da
O.S.: o que interessa é se houve improdutiva perto da visita que vão fazer
agora. Uma O.S. aberta há dois meses e remarcada para amanhã continua
valendo a conferida.

**A primeira vez que uma O.S. aparece nunca gera aviso de remarcação, só
registro.** Vale para a que acabou de entrar (o aviso de entrante já saiu, e
dois seguidos seriam ruído) e para as que já estavam em campo quando o
recurso foi publicado — sem essa regra, a primeira varredura despejaria uma
remarcação falsa para cada O.S. aberta do litoral e do RJ de uma vez só.

O que o bot lembra fica em `<bot>/dados/agendamentos_vistos.json`
(`{os_id: último agendamento visto}`). **Apagar esse arquivo não gera
enxurrada**: sem ele, toda O.S. volta a ser "primeira vez" e o bot fica
quieto até a próxima remarcação de verdade.

---

## 2. As duas chaves e a janela

| | |
|---|---|
| Casa por | **contrato** ou **nome** (nesta ordem de prioridade) |
| Janela | **30 dias** para trás, a partir da abertura do entrante |
| Devolve | a improdutiva **mais recente** dentro da janela |

O **endereço ficou de fora** por decisão de 13/08/2026: o número da rua só
existe dentro de `ordemServicos[]` do lado do CAMPO e grudado num texto único
(`RUA X, 41 BAIRRO, CIDADE - UF`) do lado do OFS. Casar isso exigiria
adivinhar formato dos dois lados, e o retorno não pagava o risco de errar.

O nome é normalizado antes de comparar (sem acento, caixa alta, espaço
colapsado), senão `JOSÉ DA SILVA` e `JOSE DA  SILVA` seriam duas pessoas.

---

## 3. Quais motivos alertam

É **lista de exclusão**, não de inclusão: alerta toda improdutiva, **menos**
as que estão em `MOTIVOS_SEM_ALERTA` (em `bot/improdutivas.py`).

A régua não é a origem (TÉCNICA/COMERCIAL/CLIENTE) e sim: *esta visita
perdida diz alguma coisa sobre a próxima?*

### Não alertam (decisão da operação, 13/08/2026)

| Motivo | Por quê |
|---|---|
| Cliente ausente | o cliente vai remarcar, e pronto |
| Solicitação de reagendamento | idem |
| Reagendou | idem |
| Chuva | é do dia, não do caso |
| Falta material | idem |
| Não cumprimento de agenda | falha de execução, não do cliente |

### Alertam — o que a primeira base real trouxe (32 dias)

| Motivo | Linhas |
|---|---|
| Problema de infraestrutura | 158 |
| Problema CTO | 73 |
| Endereço não localizado | 63 |
| Entrada não autorizada | 53 |
| Tubulação obstruída | 25 |
| Situação de risco | 17 |
| Falha massiva | 13 |
| Área de risco | 12 |
| Desistiu do serviço | 12 |
| Interna cliente | 10 |
| Endereço incorreto | 9 |
| Abertura indevida | 9 |

Total: **455 alertáveis** de 762 improdutivas — as outras 307 caíram na
exclusão (só `Cliente ausente` eram 199).

### Os dois vocabulários

A tabela de classificação conhece o mesmo motivo escrito de dois jeitos:

- `Interna cliente` — grafia do **OFS**, é a que aparece na base e a que
  vale na prática;
- `CLIENTE - INTERNA CLIENTE` — grafia do **CAMPO**, onde o chamado traz
  `motivoConclusao` e `submotivoConclusao` separados.

A do CAMPO fica como rede de proteção: se um dia a exportação mudar de
formato, os motivos casam sozinhos em vez de o bot emudecer.

---

## 4. A base de 30 dias

**É um arquivo separado da Base OFS, e tem de continuar sendo.**

| | `base OFS ok.xlsx` | `base improdutivas 30 dias.xlsx` |
|---|---|---|
| Serve a | garantia de reparo | alerta de reincidência |
| Exportação | **só** atividades concluídas | **todos** os status |
| Status na prática | `concluído` | `não concluído`, `cancelado`, `suspenso`, `pendente`, `concluído`, … |

A base de garantia é crítica e funciona; misturar as duas seria mexer nela
para economizar um arquivo. São dois carregadores independentes no bot e não
há cruzamento entre eles.

> ⚠️ **A exportação tem de sair SEM o filtro de status.** Com o filtro, a
> base inteira vira produtiva, nada quebra, nenhum erro aparece — e o bot
> simplesmente nunca alerta. Foi assim que o recurso quase nasceu morto: a
> primeira base que olhamos tinha 5.586 linhas e zero improdutivas.

**Como atualizar:** página **Garantias** do site → enviar a planilha. O site
grava, espelha para `<bot>/dados`, e o bot percebe o `mtime` e recarrega
sozinho. Não precisa reiniciar nada.

### Formatos aceitos

O site reconhece o arquivo **pela assinatura, não pela extensão** (quem
exporta renomeia, e CSV salvo como `.xlsx` é o engano mais comum), e
converte para `.xlsx` na gravação — assim o bot abre um formato só.

| Formato | |
|---|---|
| `.xlsx` / `.xlsm` | aceito, gravado byte a byte |
| CSV | aceito e convertido |
| `.xls` antigo, `.xlsb`, `.ods` | **recusado com recado**: falta o pacote (`xlrd`, `pyxlsb`, `odfpy`) |

Se o OFS passar a exportar `.xls`, resolve-se instalando o `xlrd` no venv do
servidor.

---

## 5. Quando um motivo novo aparecer

Motivo que não está em nenhuma das duas tabelas **não gera alerta** — sem
saber se foi produtivo, chutar viraria alarme falso no grupo. Mas ele **vai
para o log**, que é o único jeito de descobrir que o OFS mudou o
vocabulário em vez de o bot ficar quieto:

```bash
ssh operador@provedor.example 'sudo journalctl -u campo-bot --since today --no-pager | grep -i improdutiv'
```

Foi exatamente assim que `Limpeza de conector externo` (produtiva, irmã da
interna) e `Suspeita de fraude` (improdutiva) foram encontrados na primeira
base real — um caso cada.

Para acrescentar um motivo, dois lugares em `bot/improdutivas.py`:

1. **`MOTIVO_PRODUTIVO`** — `True` se for produtivo, `False` se improdutivo;
2. **`MAPEAMENTO_MOTIVOS`** — a origem (TÉCNICA / COMERCIAL / CLIENTE);
3. e, se for improdutivo mas não merecer alerta, **`MOTIVOS_SEM_ALERTA`**.

---

## 6. Se o alerta parar de sair

Na ordem:

1. **A base está velha?** Página Garantias mostra a idade de cada planilha.
   Ela cobre 30 dias — desatualizada, vai perdendo casos pela borda.
2. **A base tem improdutivas?** Procure no log a linha "Base de improdutivas
   carregada". Se disser `NENHUM motivo que gere alerta`, a exportação saiu
   filtrada (ver seção 4).
3. **Motivos fora das tabelas?** Mesmo log, seção 5.
4. **O contador do dia:** mande `/status` no grupo — tem a linha
   "Improdutivas notificadas hoje". Ela soma os dois tipos de aviso.
5. **Só a remarcação parou?** Procure no log
   `OS com agendamento já conhecido`. Se vier `0` toda vez que o bot sobe, o
   `agendamentos_vistos.json` não está sendo gravado — e aí toda O.S. é
   sempre "primeira vez", que nunca alerta.

---

## 7. O que foi aposentado

O comando **`/improdutivas`** não existe mais. Era um relatório de lote:
alguém mandava o comando no grupo do WhatsApp, anexava o CSV do OFS e
recebia listas por região. Não tinha memória e não cruzava com nada —
analisava, imprimia e esquecia.

Saíram com ele: a espera por anexo no grupo, a formatação por região e a
quebra de mensagem longa. O bot passou a ignorar anexos no grupo; as bases
entram pelo site.

**Sobreviveram os dois dicionários de classificação**, que são a mesma
tabela da visão "Efetividade Geral" do `operacional.py` — e são eles que
respondem a única pergunta que interessa agora.

---

## 8. Onde está cada coisa

| | |
|---|---|
| Regra, tabelas e índice | `bot/improdutivas.py` |
| Consulta e mensagem | `bot/bot_campo_monitoramento.py` (`verificar_improdutiva_anterior`, `notificar_improdutiva_telegram`) |
| Gancho no entrante | mesmo arquivo, no bloco `if codigo in CODIGOS_ALVO` |
| Gancho na remarcação | mesmo bloco, `acompanhar_remarcacao` — **fora** do `if os_id not in os_notificadas`, que é o ponto todo |
| Memória do agendamento | `<bot>/dados/agendamentos_vistos.json` |
| Registro da planilha | `site/web/fontes/planilhas.py` (`ARQUIVOS`, `ESPELHADOS_NO_BOT`) |
| Aviso de base faltando | `site/web/config.py` (`problemas()`) |
