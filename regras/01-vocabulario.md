# VOCABULÁRIO DA OPERAÇÃO

Use estes termos exatamente com estes sentidos. Quem pergunta no grupo usa as
palavras daqui, não as do manual.

## O que é uma O.S.

**O.S.** -- ordem de serviço. A unidade de trabalho: uma visita a fazer.

**Nota**, ou **nota de serviço** -- é o mesmo que O.S. É como a operação chama
no dia a dia. "Quantas notas o técnico tem hoje" quer dizer quantas O.S.
Sem isto escrito, uma pergunta com "nota" recebia a resposta de que não havia
notas, quando havia todas.

**Chamado** -- o registro no CAMPO. Um chamado pode conter mais de uma O.S.
Contar chamados e contar O.S. dá números diferentes, e a pergunta quase sempre
é sobre O.S.

**Atividade** -- como o OFS chama a mesma coisa do lado do campo. Uma
atividade é uma tentativa: o mesmo serviço que voltou três vezes aparece como
três atividades. Contar linhas do OFS não conta serviços distintos.

**Contrato** -- o cliente, do ponto de vista do sistema. É a chave que liga
CAMPO, OFS e Autenticador. Um contrato tem histórico; uma O.S., não.

## Estados de agendamento

**D0** -- agendado para hoje. **D+1**, **D+2**, **D+3** -- para os próximos
dias.

**Vencida**, ou **atrasada** -- a data agendada já passou e a O.S. continua
aberta.

**Sem agenda** -- a O.S. existe e ninguém marcou dia. Não é o mesmo que
vencida, e confundir as duas foi um erro real: uma O.S. sem agenda nunca
esteve atrasada, porque nunca teve prazo.

**Enviado D0** -- a O.S. está na agenda de hoje, seja pelo agendamento do
próprio CAMPO, seja porque o contrato aparece no OFS GERAL de hoje. A segunda
metade não é detalhe: O.S. que deu erro de integração é puxada à mão pelo
OFS, e sem esse cruzamento o número sai menor que o real.

**Conveniência** -- não é D0, mas o contrato está na planilha de conveniência.

**Oportunidade de injeção** -- nem D0 nem conveniência. É o que dá para puxar
para frente: agendamento futuro, vencida, e O.S. ainda sem agenda. É a
resposta para "o que dá para adiantar".

## Idade

**Bucket de idade**, ou **balde** -- há quanto tempo a O.S. está aberta,
contado da abertura. São três faixas, e **os limites mudam conforme a
categoria** -- ver `03-categorias-e-prazos.md`. Um reparo com 3 dias e uma
ativação com 3 dias não estão no mesmo balde.

**Véspera de balde** -- a O.S. que muda de faixa amanhã. Serve para priorizar
hoje o que piora amanhã.

## Resultado da visita

**Produtiva** -- a visita resolveu.

**Improdutiva** -- a visita não resolveu. Interessa a improdutiva de origem
**técnica**; as de origem comercial ou do cliente contam para outra conta.

**Improdutiva reincidente** -- O.S. aberta cujo contrato já teve visita
improdutiva antes, dentro da janela da base. É o sinal de que mandar o
técnico de novo do mesmo jeito tende a dar no mesmo.

**Garantia** -- reparo que voltou dentro do prazo de garantia do atendimento
anterior. Na tela e nos rótulos aparece como **IRR**, **IFI** e
**IFI de MDE**, que são os mesmos nomes usados no site, de propósito.

## Coisas do campo

**Unidade**, ou **praça** -- a sigla da base: CGT, VRD, SST. Ver
`02-unidades.md`.

**Fila** -- o código que diz o tipo de serviço da O.S. (ES02, ES05, UP02...).
É dele que sai a categoria.

**Recurso** -- no OFS, quem executa. Quase sempre é um técnico, mas **nem
sempre é pessoa**: CARAGUATATUBA é uma fila de praça, não um sujeito. Dizer
"o técnico CARAGUATATUBA" é erro.

**Pacote** -- a última O.S. do chamado já tem material definido. O.S. na
agenda de hoje **sem pacote** é candidata a improdutiva, porque o técnico sai
sem o que instalar.

**Área de risco** -- endereço que cai dentro do mapa de risco da operação do
Rio, ou que está na lista de ruas marcadas. Muda como se atende, não se se
atende.

**Entrante** -- O.S. que entrou hoje. O **termômetro** compara as entradas de
hoje com a média histórica: serve para dizer se o backlog cresceu porque
entrou muito ou porque saiu pouco.

**Autenticador** -- diz se o contrato está online ou offline agora. Só faz sentido
para reparo: um cliente que responde ao ping raramente precisa de visita.
