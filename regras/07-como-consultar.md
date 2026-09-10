# COMO SE CONSULTA CADA SISTEMA

Passo a passo do mecanismo real. Cada seção diz: como se autentica, como se
pede, o que volta, e **como aquele sistema falha** -- porque nenhum dos três
falha devolvendo erro.

---

## CAMPO -- o backlog de chamados

### Como se autentica

O CAMPO não tem API pública nem chave. O bot abre `https://campo.provedor.example/login/`
num Chromium com perfil persistente, entra por "Entrar com login Provedor", e a
sessão fica guardada no perfil. Quando o login pede código do autenticador, é
**humano**: alguém digita.

O token não é pedido -- é **capturado**. Um ouvinte do Playwright observa as
requisições da própria página e, quando vê um `PUT` para uma URL com `chamado`,
guarda três coisas:

```
estado_sessao['url_chamados_base']   # a URL do endpoint
estado_sessao['token_atual']         # o header 'token'
estado_sessao['corpo_filtro_atual']  # o corpo do filtro
```

Só isso é lido da resposta. **O corpo da resposta nunca é lido pelo ouvinte**:
chamar `.json()` ali fazia a estrutura parseada inteira ficar retida pelo canal
do Playwright -- 12 rodadas foram de 33 MB para 788 MB, de 40 mil para 1 milhão
de objetos vivos.

### Como se pede

Com token e URL na mão, a varredura sai **por fora do navegador**, com
`requests`. Não é preferência de estilo: medido com a mesma carga, o caminho do
Playwright levou 30 MB a 463 MB, e o `requests` de 28 MB a 34 MB, e ainda é
~2,6x mais rápido por requisição.

São **duas buscas**, paginadas:

```python
# Busca 1 -- CAPEX + reparo nas unidades que roteirizamos
{
  "enderecoUnidade": LITORAL_SP + RJ,
  "dataConclusao": "IS NULL",
  "fila_codigo": [ES02, ES02PV, ATV1, ..., ES05, ES06, REPPME, UP02, ES15],
  "contrato": None
}

# Busca 2 -- só reparo, nas siglas de fora da área (SIGLAS_GARANTIA_EXTRA)
{
  "enderecoUnidade": list(SIGLAS_GARANTIA_EXTRA),
  "dataConclusao": "IS NULL",
  "fila_codigo": ["ES05", "REPPME"],
  "contrato": None
}
```

`dataConclusao: "IS NULL"` é o filtro que define "aberto". É por isso que o CAMPO
**não alcança nada concluído** -- não é limitação de leitura, é o filtro.

**Armadilha:** `enderecoUnidade: []` não quer dizer "nenhuma unidade". Quer
dizer **o país inteiro**. Por isso a busca 2 só sai se a lista não estiver
vazia.

### Como isso falha

Truncando em silêncio. A paginação pode voltar incompleta, e um conjunto
subcontado faz a reavaliação concluir que reparo aberto foi fechado -- calando
garantia de verdade. Em 14/08/2026 a busca 2 voltou com 290 de ~1.419 e a
varredura foi tratada como completa: 673 chamados no lugar de 1.684, sem um
aviso.

Por isso existem **duas** bandeiras de confiança, e não uma:

- `capex_confiavel` -- a busca 1 percorreu todas as páginas;
- `reparos_confiavel` -- **as duas** vieram inteiras.

Amarrar as duas erraria nos dois sentidos.

### Ritmo

Varredura completa a cada `INTERVALO_VARREDURA_COMPLETA_SEG` = **45 s**.
Não é 25 s por escolha medida: 25 s daria ~1.760 varreduras por turno, 7x as
requisições no CAMPO -- e o CAMPO não é nosso. 45 s dá ~880 (3,6x) e já entrega
mais da metade do ganho.

O resultado da varredura passa por `projetar_para_cache()`, que guarda só 10
dos 46 campos. A lista completa ocupava 36,9 MB contra 4,6 MB da projeção.

---

## AUTENTICADOR -- o contrato está online agora?

### Como se autentica

Pela **VPN**. Não há login: o servidor só responde para quem está dentro do
túnel. Se a VPN cair, a resposta vem sem a tabela, e a mensagem de erro diz
exatamente isso.

### Como se pede

Três chamadas em sequência, sempre nesta ordem:

```
POST https://provedor.example/status.php?action=save     # manda a lista
GET  https://provedor.example/processa.php?bg=1          # manda processar
GET  https://provedor.example/ler_csv.php                # lê o resultado
```

O corpo do POST é `{"contratos": "um\npor\nlinha"}`.

### O que volta

Uma tabela HTML com as colunas `contrato, username, acctstarttime,
acctstoptime, circuitid, callingstationid, trafego, servidor`, renomeadas para
`CONTRATO, USERNAME, INÍCIO, FIM, CIRCUITO, MAC, TRÁFEGO, SERVIDOR`.

O status sai de uma regra só: **sessão com `FIM` vazio = ONLINE**. Sem
nenhuma sessão na tabela = `NÃO LOCALIZADO`. Com sessões, todas terminadas =
`OFFLINE`.

### Como isso falha -- e esta é a armadilha mais séria dos três sistemas

**O CSV de resultado é UM arquivo só no servidor, compartilhado por todos os
usuários da ferramenta.** Quando outra pessoa dispara uma consulta, ela
sobrescreve a nossa.

O sintoma: a leitura devolve contratos que não pedimos e, logo depois, um
arquivo vazio enquanto está sendo reescrito. Sem tratar, **todo contrato vira
NÃO LOCALIZADO -- indistinguível de cliente sem sessão.** É a causa de o status
oscilar entre ONLINE e NÃO LOCALIZADO sem nada mudar na rede.

Reler não resolve, porque o pedido em si foi atropelado. O que resolve é
**refazer o ciclo inteiro**, e é o que `_consultar_autenticador_com_retentativa` faz,
até 3 vezes:

- achou contrato que ninguém pediu → o arquivo é de outra consulta, refaz;
- tabela vazia → **não é resposta**, é o instante da reescrita. Tratá-la como
  resultado engolia a retentativa;
- tabela estável faltando alguém → aceita o que veio.

---

## OFS -- a agenda de campo

### Como se autentica

Por **cookies de uma sessão que uma pessoa abriu**. O login do OFS pede código
(MFA), então é sempre humano -- mesmo arranjo do CAMPO.

```bash
python ofs_extracao.py login    # abre o navegador e espera VOCÊ entrar
python ofs_extracao.py baixar   # usa só os cookies guardados, sem navegador
```

A sessão **não é amarrada ao IP**: um `cookies.json` gerado no Windows funciona
no servidor Linux -- medido em 20/08/2026, mesmas 330 atividades nas duas
máquinas.

### Como se pede

Não existe botão nem tela para automatizar. Cada área é **um GET só**:

```
https://campo.provedor.example/?m=gridexport&a=download&itype=manage
    &providerId=<área>&date=<AAAA-MM-DD>&panel=top&view=time
    &downloadId=<qualquer>&dates=<AAAA-MM-DD>&recursively=1
```

"TODOS + CSV consolidado" é baixar as duas áreas e juntar mantendo **um único
cabeçalho**.

### Como isso falha

**Sessão vencida NÃO vem como erro.** O OFS responde `200 OK` com corpo
**vazio** e um cabeçalho `refresh: ...force=logout`.

Quem não conferir o corpo gera o painel sobre uma base vazia sem receber aviso
nenhum. Por isso toda resposta passa por `validar_csv()` antes de virar
arquivo, e "vazio" tem nome próprio: `SessaoVencida`.

### Os três arquivos que saem daí

| arquivo | o que é | quem lê |
|---|---|---|
| `dados/OPERACIONAL.csv` | a exportação do dia | painel de resultados |
| `dados/OFS GERAL.csv` | agenda de hoje e amanhã, por contrato | "Enviado D0" do backlog, confirmação de agenda do site |
| base histórica | o acumulado das exportações | improdutivas, garantias, e as buscas do `/bot` |

O `OFS GERAL.csv` fica na pasta do **bot**, não do site. Já ficou do outro lado,
e o bot o procurava seguindo um `painel_config.json` que não existe no Linux --
resultado medido em 26/08/2026: procurava num lugar e o arquivo estava noutro.

Há **idade máxima** para o cruzamento valer (`backlog_ofs.py`): arquivo velho
demais não cruza, em vez de cruzar com a agenda de ontem.
