<#
    Registra a tarefa do Agendador que mantem o bot CAMPO no ar.

    RODE NA MAQUINA DE PRODUCAO (PRODUCAO), como ADMINISTRADOR, uma vez so:
        powershell -ExecutionPolicy Bypass -File .\instalar_tarefa.ps1

    ---------------------------------------------------------------------
    COMO ISSO SUBSTITUI O reiniciar_bot.bat

    O .bat era uma janela do CMD que ficava num laco 'goto'. Em 07/08/2026 esse
    goto falhou -- o CMD nao conseguiu reler o proprio arquivo do disco enquanto
    a maquina estava travada de I/O -- e, como o CMD ENCERRA o .bat quando um
    goto falha, o supervisor morreu calado e o bot ficou horas fora do ar.

    O Agendador nao relê arquivo nenhum para decidir reiniciar, e o servico dele
    e parte do nucleo do Windows. Alem disso ele cobre casos que nenhum
    supervisor interno cobre: processo morto no Gerenciador, falta de memoria,
    e a maquina reiniciando.

    O SEGREDO esta em MultipleInstances = IgnoreNew, que ja e o padrao:
    a cada 5 min o Windows TENTA subir o bot; se ja houver um rodando, o
    disparo e ignorado em silencio. Por isso esta tarefa e o relancamento
    interno do proprio bot convivem sem precisar combinar nada entre si --
    quem chegar primeiro sobe, o outro simplesmente perde a vez.

    Historico que justifica o piso de 5 min (recuperacoes ANTES disso existir):
        05/08 06:13  ->  voltou 06:33          (20 min, na mao)
        05/08 21:48  ->  voltou 06/08 06:55    (9h07min, na mao)
        06/08 17:12  ->  voltou 17:14          (2 min, na mao)
        07/08 16:35  ->  nunca voltou
#>

$ErrorActionPreference = "Stop"

$NOME_TAREFA = "Bot CAMPO - monitor"
$PASTA = $PSScriptRoot
$EXE = Join-Path $PASTA "bot_campo_monitoramento.exe"

Write-Host ""
Write-Host "=== Instalando a tarefa do bot CAMPO ===" -ForegroundColor Cyan
Write-Host "Pasta : $PASTA"

# --- 1. E administrador? Register-ScheduledTask com -RunLevel Highest exige. ---
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
$souAdmin = (New-Object Security.Principal.WindowsPrincipal $id).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $souAdmin) {
    Write-Host ""
    Write-Host "[ERRO] Rode este script como Administrador." -ForegroundColor Red
    Write-Host "       Clique com o botao direito no PowerShell > Executar como administrador."
    exit 1
}

# --- 2. O .exe esta do lado? ---
if (-not (Test-Path $EXE)) {
    Write-Host ""
    Write-Host "[ERRO] Nao encontrei $EXE" -ForegroundColor Red
    Write-Host "       Este script tem que ficar NA MESMA PASTA do bot_campo_monitoramento.exe."
    exit 1
}
Write-Host "Exe   : $EXE  (ok)"

# --- 3. Sai da frente de qualquer tarefa antiga ---
# Procura pelo QUE A TAREFA EXECUTA, nao pelo nome dela: quem criou a tarefa
# antiga na mao pode ter posto qualquer nome, e uma tarefa esquecida apontando
# para o reiniciar_bot.bat -- que nao existe mais -- falharia calada para sempre.
$alvos = @("reiniciar_bot.bat", "bot_campo_monitoramento.exe")
$antigas = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
    $t = $_
    ($t.TaskName -eq $NOME_TAREFA) -or
    ($t.Actions | Where-Object {
        $exec = $_.Execute
        $exec -and ($alvos | Where-Object { $exec -like "*$_*" })
    })
})

if ($antigas.Count -eq 0) {
    Write-Host "Nenhuma tarefa anterior encontrada."
} else {
    foreach ($t in $antigas) {
        $oque = ($t.Actions | ForEach-Object { $_.Execute }) -join "; "
        Unregister-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath -Confirm:$false
        Write-Host ("Removida a tarefa anterior: '" + $t.TaskName + "'  ->  " + $oque) -ForegroundColor Yellow
    }
}

# --- 4. Monta a tarefa ---
# -WorkingDirectory e OBRIGATORIO: o bot resolve dados/, logs/, relatorios/ e
# perfil_campo_logistica/ pelo diretorio atual. Sem isso, iniciado pelo Agendador
# (que roda em C:\Windows\system32) ele sobe com 0 OS notificadas, sem a base
# OFS, sem node_modules e com perfil de navegador vazio pedindo MFA.
$acao = New-ScheduledTaskAction -Execute $EXE -WorkingDirectory $PASTA

# DOIS gatilhos, e o de baixo e o que sustenta tudo.
#
# O gatilho que carrega a repeticao TEM de ser um -Once com inicio no PASSADO.
# Pendurar a repeticao num -AtLogOn NAO funciona: a repeticao so comeca a contar
# quando o gatilho dispara, ou seja, no proximo logon. Registrando a tarefa com
# o usuario JA logado, ela nunca arma -- o NextRunTime fica vazio e a rede de
# 5 min simplesmente nao existe, calada.
#
# Foi o que aconteceu em 08/08/2026: a tarefa foi registrada as 19:13 do dia
# anterior com o usuario ja logado, rodou uma vez na mao, e quando o bot travou
# as 01:37 nao havia repeticao nenhuma para reergue-lo. Ficou 1h20 fora do ar.
# Medido depois: 'AtLogOn + Repetition' -> NextRunTime vazio;
#                'Once no passado + 5min' -> NextRunTime preenchido.
#
# O -AtLogOn fica junto so para subir mais rapido depois de um reboot; quem
# garante o piso e o -Once.
$gatilhoLogon = New-ScheduledTaskTrigger -AtLogOn
$gatilhoRepete = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(-2) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$gatilho = @($gatilhoLogon, $gatilhoRepete)

# ExecutionTimeLimit 0 = sem limite. O padrao e 72h, e ao fim delas o Agendador
# MATARIA o bot saudavel no meio do expediente.
$config = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew

# O bot usa tkinter e automatiza a janela do FortiClient, entao PRECISA de
# sessao grafica -- nao pode rodar como servico (sessao 0). Por isso
# -LogonType Interactive: so roda com o usuario logado, igual e hoje.
$usuario = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $usuario `
    -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $NOME_TAREFA -Action $acao -Trigger $gatilho `
    -Settings $config -Principal $principal `
    -Description "Mantem o bot CAMPO no ar. Tenta subir a cada 5 min; ignora o disparo se ja estiver rodando." | Out-Null

# --- 5. Confere o que ficou gravado, em vez de confiar ---
$t = Get-ScheduledTask -TaskName $NOME_TAREFA
Write-Host ""
Write-Host "=== Tarefa registrada ===" -ForegroundColor Green
Write-Host ("  Nome ................: " + $t.TaskName)
Write-Host ("  Executavel ..........: " + $t.Actions.Execute)
Write-Host ("  Pasta de trabalho ...: " + $t.Actions.WorkingDirectory)
Write-Host ("  Repete a cada .......: " + $t.Triggers.Repetition.Interval)
Write-Host ("  Por quanto tempo ....: '" + $t.Triggers.Repetition.Duration + "'  (vazio = para sempre)")
Write-Host ("  Se ja estiver rodando: " + $t.Settings.MultipleInstances + "  (IgnoreNew = pula a vez)")
Write-Host ("  Limite de execucao ..: " + $t.Settings.ExecutionTimeLimit + "  (PT0S = sem limite)")
Write-Host ("  Usuario .............: " + $t.Principal.UserId + " / " + $t.Principal.RunLevel)

# --- 6. A VERIFICACAO QUE IMPORTA ---
# NextRunTime vazio significa que NAO existe proxima execucao agendada, ou seja,
# a rede de 5 minutos nao esta armada -- a tarefa so rodaria se alguem mandasse
# na mao. Foi exatamente assim que a tarefa ficou de 07/08 19:13 ate 08/08:
# aparentemente instalada, imprimindo tudo certo, e sem nenhuma rede por tras.
# Por isso isto aqui e ERRO e nao aviso: instalacao sem NextRunTime nao serve.
$info = Get-ScheduledTaskInfo -TaskName $NOME_TAREFA
Write-Host ""
if (-not $info.NextRunTime) {
    Write-Host "=== FALHOU ===" -ForegroundColor Red
    Write-Host "  NextRunTime esta VAZIO: a repeticao de 5 min NAO foi armada." -ForegroundColor Red
    Write-Host "  A tarefa existe mas nao vai reerguer o bot sozinho."
    Write-Host "  NAO considere instalado. Me avise com esta mensagem."
    exit 1
}
Write-Host "=== Rede de recuperacao ARMADA ===" -ForegroundColor Green
Write-Host ("  Proxima tentativa em : " + $info.NextRunTime)
Write-Host ("  Ultimo resultado ....: " + $info.LastTaskResult)
Write-Host "  A partir de agora o Windows tenta subir o bot a cada 5 min."
Write-Host "  Se ja houver um rodando, ele pula a vez -- nao sobe dois."

Write-Host ""
Write-Host "Para subir o bot agora:" -ForegroundColor Cyan
Write-Host "  Start-ScheduledTask -TaskName '$NOME_TAREFA'"
Write-Host ""
Write-Host "Para PARAR o bot de proposito (senao ele volta em ate 5 min):" -ForegroundColor Yellow
Write-Host "  Disable-ScheduledTask -TaskName '$NOME_TAREFA'"
Write-Host "  Stop-ScheduledTask    -TaskName '$NOME_TAREFA'"
Write-Host "E para religar depois:"
Write-Host "  Enable-ScheduledTask  -TaskName '$NOME_TAREFA'"
Write-Host ""
Write-Host "O reiniciar_bot.bat nao e mais usado. Pode fechar a janela dele." -ForegroundColor Yellow
Write-Host ""
