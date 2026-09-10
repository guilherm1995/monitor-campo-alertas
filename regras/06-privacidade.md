# DADOS DO CLIENTE: o que sai, para onde, e por quê

## A decisão

**O `/bot` entrega dado de cliente.** Nome, endereço, telefone, celular,
e-mail e CEP. Decisão do operador, em 30/08/2026.

A razão é operacional e é boa: quem liga para confirmar a agenda precisa do
telefone, e quem vai até o cliente precisa do endereço. Além disso o bot **já
mandava** nome, bairro e telefone nos alertas individuais de garantia,
improdutiva e área de risco -- o assistente estar mais restrito que os próprios
alertas era incoerente, e obrigava a operação a sair do grupo para consultar o
que o bot tinha na mão.

A instrução manda entregar sem recusar, sem aviso de privacidade e sem
entregar pela metade.

## Onde cada campo está

Isto **não** é uma regra de permissão, é o mecanismo. Os campos não estão
todos no mesmo lugar, e por isso a resposta a algumas perguntas exige busca:

| campo | no dossiê? | na busca? | de onde vem |
|---|---|---|---|
| nome do cliente | **sim** | sim | cache do CAMPO / OFS |
| logradouro | **sim** | sim | cache do CAMPO / OFS |
| bairro | **sim** | sim | cache do CAMPO / OFS |
| cidade | sim | sim | CAMPO / OFS |
| endereço completo | não | **sim** | OFS |
| telefone e celular | não | **sim** | OFS |
| e-mail | não | **sim** | OFS |
| CEP | não | **sim** | OFS |

Telefone, e-mail e CEP não estão no dossiê por um motivo técnico, não por
política: **eles não existem na lista que fica em memória.** A projeção do
cache do CAMPO (`CAMPOS_CACHE_BACKLOG`) guarda 10 dos 46 campos do chamado, e o
telefone só existe no chamado cru, na estrutura aninhada que a projeção
descarta. Quem os tem é a exportação do OFS -- que é o que a busca lê.

## O que isso custa, medido

Pôr nome e logradouro no dossiê fez ele crescer:

```
antes    52.580 caracteres   (~13.000 tokens)
depois   78.830 caracteres   (~20.000 tokens)
```

Isso é por pergunta, **inclusive nas que não falam de cliente nenhum**. Vale
saber por dois motivos: gasta cota, e alguns provedores tratam o teto de
tokens por minuto como teto de tamanho por requisição -- o Groq já nos recusa
com o dossiê menor.

E apertou. Com o motor pequeno do 9router, medido em 30/08/2026 logo depois
da mudança: um caso da avaliação que passava sempre virou intermitente (1 de 3
corridas), e a latência triplicou -- "qual é a capital da França" foi de 1,7 s
para 92,7 s.

Por isso a coisa virou uma chave, e não uma edição de código:

```
DOSSIE_CLIENTE=1   # padrão: cliente e logradouro na tabela de O.S.
DOSSIE_CLIENTE=0   # tabela enxuta; o dado continua nas buscas
```

**Desligar não esconde nada.** Nome, endereço, telefone, CEP e e-mail
continuam saindo pelas buscas -- que é onde telefone e CEP sempre estiveram. O
que muda é que o assistente passa a precisar pedir em vez de já ter na mão, e
o cabeçalho da tabela passa a dizer isso a ele com todas as letras: "você TEM
acesso a eles: use a busca. Não responda que não tem o dado -- procure."

Com motor grande, deixe ligado.

## Para onde esses dados vão

Para o provedor de IA, junto com a pergunta. Hoje é o 9router rodando no nosso
servidor, em `127.0.0.1:20128` -- mas o 9router é um **roteador**: ele
encaminha para o provedor de fora que estiver configurado no combo.

Duas coisas que decorrem disso, e que devem ser sabidas antes e não depois:

1. **Degrau gratuito costuma permitir treino sobre o que recebe.** É a moeda
   de troca do plano gratuito. Trocar o motor por um pago muda isso;
   trocar por outro gratuito, não.
2. **Trocar o combo troca o destino desses dados.** Um combo novo pode mandar
   nome e telefone de cliente para um provedor diferente, sem que nada no
   código mude e sem aviso nenhum.

## A lista branca continua existindo

`COLUNAS_LIBERADAS`, no `busca_operacao.py`, segue sendo uma lista **branca**:
coluna nova que apareça na exportação do OFS fica de fora até alguém decidir
que ela pode sair.

O que mudou em 30/08/2026 foi o **conteúdo** da lista, não o critério. A lista
existe para que a decisão sobre um campo novo seja tomada por alguém, uma vez,
em vez de acontecer sozinha porque a exportação ganhou uma coluna.

## Segredos no fonte

A chave do motor de IA **não** fica no código: vive em
`/etc/systemd/system/campo-bot.service.d/ia.conf`, modo 600, e chega ao processo
como variável de ambiente. A árvore do bot é espelhada num portfólio público,
e chave em código é chave revogada.
