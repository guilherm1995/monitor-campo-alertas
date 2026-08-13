# Painel de TV: quando a janela não cabe na tela

> Escrito em **13/08/2026**, depois do segundo caso. Se o painel voltar a
> aparecer cortado na TV, comece por aqui.

---

## 1. O sintoma

O Painel de TV mostra **só um pedaço de si mesmo**. A forma mais comum:

- a coluna **ENTRANTES DE CAPEX** ocupa a largura inteira da TV;
- a coluna **GARANTIAS** simplesmente não aparece;
- **relógio, data e clima** somem do topo (ficam à direita);
- os cards parecem **esticados**: letra do tamanho de sempre, card enorme,
  com um vazio grande à direita de cada um.

Nada disso é a TV cortando a imagem. A TV está mostrando **tudo** o que
recebe — o que falta nunca foi mandado para ela. A janela do painel está
maior que a tela, e o que sobra fica fora.

---

## 2. Como confirmar em 30 segundos

Meça as duas coisas e compare. Se os números forem diferentes, é este
problema:

```bash
# tamanho da tela agora
ssh operador@provedor.example 'sudo -u operacional env DISPLAY=:0 \
  XAUTHORITY=/opt/operacional/.Xauthority xrandr --query | head -1'
```

```bash
# tamanho da janela do painel (o script está na seção 6)
ssh operador@provedor.example 'sudo -u operacional env DISPLAY=:0 \
  XAUTHORITY=/opt/operacional/.Xauthority /opt/operacional/venv/bin/python3 -' < medir_janelas.py
```

No caso de 13/08/2026 a resposta foi:

```
RAIZ: 1366x768
  0x00400a40  2732x768+0+0   Central de Monitoramento - CAMPO Logística
```

**2732 = 1366 × 2.** A janela tinha o dobro da largura da tela, e a TV
mostrava a metade esquerda.

---

## 3. A causa

Duas coisas somadas.

**Primeira: a tela realmente muda de tamanho depois que o X sobe.** A porta
HDMI desta máquina tem o hotplug (HPD) defeituoso e o EDID lê 0 bytes — a TV
nunca "aperta a mão" com o kernel (ver `systemd/hdmi-tv.sh`). Por isso a tela
passa por estados intermediários: em algum momento a eDP e a HDMI ficam lado
a lado e a tela lógica soma as duas larguras (1366 + 1366 = 2732). Depois o
`hdmi-tv.timer` reconfigura e a tela volta a 1366x768. **Se o painel abriu
em tela cheia durante esse intervalo, ele nasceu com 2732 de largura.**

**Segunda, e a que fazia o problema durar: `winfo_screenwidth()` mente.** O
Tk devolve o tamanho que o Xlib gravou **na abertura da conexão**. Quando o
`xrandr` muda a resolução depois disso, esse número não acompanha — o Xlib
só o corrige se o cliente pedir (`XRRUpdateConfiguration`) ao receber o
evento do RANDR, e o Tk não escuta RANDR.

Existia um vigia de geometria justamente para reencaixar a janela quando a
tela mudasse. Ele nunca disparou, e o journal não tinha uma única linha
"a tela mudou": ele comparava a medida congelada **com ela mesma**. Os dois
lados da conta vinham da mesma fonte errada. Vigia que mede com a régua
errada jura que está tudo certo.

### Por que fechar e reabrir o painel não resolve

`/ocultarpaineltv` seguido de `/exibirpaineltv` destrói o Tk e cria outro,
mas **o Tcl reaproveita a conexão X do processo** — a medida congelada vem
junto. Só o restart do `campo-bot` abria uma conexão nova. Se um dia o painel
aparecer cortado e o toggle não resolver, é este problema e não outro.

---

## 4. A correção (já aplicada em 13/08/2026)

Em `bot/bot_campo_monitoramento.py`, a função **`tamanho_real_da_tela()`**
pergunta a geometria da **janela raiz do X** por `ctypes`/libX11 — a raiz é
redimensionada de verdade quando o modo muda, então ela é a fonte de
verdade. Fora do Linux (ou se a leitura falhar) cai no valor do Tk, como
antes.

Os quatro lugares que mediam a tela passaram a usá-la:

| Onde | O que quebrava com a medida velha |
|---|---|
| `PainelTV._vigiar_geometria` | o vigia não via a mudança (o bug principal) |
| `PainelTV._aplicar_tela_cheia` | reaplicava a tela cheia no tamanho errado |
| `ColunaPainel.altura_util` | altura dos cards, quando a lista ainda não tem tamanho |
| `PainelTV._largura_conteudo_alerta` | largura do alerta — truncava o nome do cliente errado |

Com isso, se a tela mudar de tamanho outra vez, o vigia percebe **em até 5
segundos** e reencaixa a janela sozinho, sem restart e sem comando no grupo.

Backup da versão anterior no servidor:
`/opt/operacional/bot/bot_campo_monitoramento.py.bak-antes-tela-2732`

---

## 5. Se acontecer de novo

Na ordem:

1. **Meça as duas coisas** (seção 2). Se janela = tela, o problema é outro —
   veja a seção 7.
2. Se a janela for maior que a tela e a correção estiver no ar, espere
   **10 segundos**: o vigia resolve sozinho. Se não resolver, o `.py` do
   servidor está velho — compare o `md5sum` com o de
   `migracao_linux/bot/bot_campo_monitoramento.py`.
3. Último recurso: `sudo systemctl restart campo-bot`. Abre conexão nova e
   corrige na hora, mas não impede a volta.

---

## 6. Ferramentas de diagnóstico

O servidor **não tem** `scrot`, `import` (ImageMagick), `xwd`, `maim` nem
`xwininfo`. Para o **print** isso não é problema (o PIL resolve sozinho, ver
6b); para **medir a janela** não há substituto pronto, e é justamente a
medida que dá o diagnóstico — daí o script da 6a.

### 6a. Medir as janelas (substitui o `xwininfo`)

Salve como `medir_janelas.py` na sua máquina.

```python
import ctypes, sys

x11 = ctypes.CDLL("libX11.so.6")
x11.XOpenDisplay.restype = ctypes.c_void_p
x11.XDefaultRootWindow.restype = ctypes.c_ulong
x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]

display = x11.XOpenDisplay(None)
if not display:
    sys.exit("ERRO: nao abriu o DISPLAY")
raiz = x11.XDefaultRootWindow(display)

def geometria(janela):
    r = ctypes.c_ulong(); x = ctypes.c_int(); y = ctypes.c_int()
    larg = ctypes.c_uint(); alt = ctypes.c_uint()
    b = ctypes.c_uint(); p = ctypes.c_uint()
    ok = x11.XGetGeometry(ctypes.c_void_p(display), ctypes.c_ulong(janela),
                          ctypes.byref(r), ctypes.byref(x), ctypes.byref(y),
                          ctypes.byref(larg), ctypes.byref(alt),
                          ctypes.byref(b), ctypes.byref(p))
    return (x.value, y.value, larg.value, alt.value) if ok else None

def nome(janela):
    buf = ctypes.c_char_p()
    if x11.XFetchName(ctypes.c_void_p(display), ctypes.c_ulong(janela),
                      ctypes.byref(buf)) and buf.value:
        return buf.value.decode("utf-8", "replace")
    return "(sem nome)"

g = geometria(raiz)
print(f"RAIZ: {g[2]}x{g[3]}")

r = ctypes.c_ulong(); pai = ctypes.c_ulong()
filhos = ctypes.POINTER(ctypes.c_ulong)(); n = ctypes.c_uint()
x11.XQueryTree(ctypes.c_void_p(display), ctypes.c_ulong(raiz),
               ctypes.byref(r), ctypes.byref(pai),
               ctypes.byref(filhos), ctypes.byref(n))
for i in range(n.value):
    g = geometria(filhos[i])
    if g and g[2] >= 50 and g[3] >= 50:   # pula janelas de servico (1x1, IPC)
        print(f"  {filhos[i]:#010x}  {g[2]}x{g[3]}+{g[0]}+{g[1]}   {nome(filhos[i])}")
```

Uso:

```bash
ssh operador@provedor.example 'sudo -u operacional env DISPLAY=:0 XAUTHORITY=/opt/operacional/.Xauthority /opt/operacional/venv/bin/python3 -' < medir_janelas.py
```

### 6b. Tirar print da TV

O PIL traz um capturador X11 **compilado dentro dele**, que não depende de
`scrot`/`xwd` nenhum — mas só quando se passa `xdisplay` explicitamente.
`ImageGrab.grab()` sem esse argumento é que cai nas ferramentas externas e
falha aqui. Conferido no servidor em 13/08/2026.

```bash
ssh operador@provedor.example 'sudo -u operacional env DISPLAY=:0 XAUTHORITY=/opt/operacional/.Xauthority /opt/operacional/venv/bin/python3 -c "from PIL import ImageGrab; ImageGrab.grab(xdisplay=\":0\").save(\"/tmp/tela.png\")"'
```

```bash
scp operador@provedor.example:/tmp/tela.png .
```

O arquivo fica no `/tmp` do servidor **como dono `operacional`**, e o `/tmp` tem
sticky bit — apagar depois exige `sudo`, senão dá "Operation not permitted":

```bash
ssh operador@provedor.example 'sudo rm -f /tmp/tela.png'
```

Sai em 1366x768 — a resolução real do X. A TV recebe isso ampliado para
1080p pelo `--scale-from` do `hdmi-tv.sh`, então o print mostra o **conteúdo**
fiel, não o tamanho físico na parede.

> Para não deixar arquivo no servidor, dá para trocar o `save()` por um PNG
> em base64 no stdout e montar a imagem na sua máquina. Só vale a pena se o
> `/tmp` do servidor for uma preocupação; o `scp` acima é mais simples.

---

## 7. Cortes que **não** são este problema

| O que se vê | Onde está a causa |
|---|---|
| Falta uma borda fina dos **quatro** lados, e a janela mede igual à tela | **Overscan da TV.** Menu da TV: procure "ajuste de imagem / tamanho da imagem" e escolha *Tela cheia*, *Just scan*, *1:1* ou *Screen fit* (o nome muda por marca). Não é código. |
| Faixa preta em volta do painel | A TV está em modo de proporção errado, ou o `--scale-from` do `hdmi-tv.sh` não foi aplicado. Rode `sudo systemctl start hdmi-tv`. |
| Só a **última linha de cards** fica cortada embaixo | Aí é layout: são mais cards do que cabem. Ver `MAX_ITENS_CAPEX` e `MAX_ITENS_GARANTIA`. |
| TV preta, sem nada | Não é o painel. Ver `hdmi-tv.sh` e a seção 3.7 de `HISTORICO_E_STATUS.md`. |
