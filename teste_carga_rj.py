# -*- coding: utf-8 -*-
"""Ensaio da prévia da carga no Sul RJ: o recorte por sigla da workzone.

Roda sem rede e sem modelo -- só a conta. `python teste_carga_rj.py` sai com 0
quando tudo bate e com 1 quando algo foge. Ao mexer em `carga_rj.py` ou no
`Recorte` do `carga_litoral.py`, rode isto E o `teste_carga.py` antes de
instalar: os dois recortes passam pelo mesmo motor, e quebrar um mexendo no
outro é exatamente o risco que estes dois arquivos existem para pegar.

O QUE ESTÁ SENDO CONFERIDO
--------------------------
1. a cidade vem da SIGLA, e aparece com o nome ("VRD" -> "Volta Redonda");
2. linha cuja `Cidade` discorda da sigla segue a SIGLA -- é a rota que atende;
3. sigla do Litoral não entra na prévia do Rio, e vice-versa;
4. as exclusões de sempre continuam valendo (cancelado, tipo fora da carga,
   outro dia);
5. `RESENDE` e `TRES RIOS` marcam `no_balde`; nome de técnico não marca;
6. base SEM a coluna `Chave Workzone` devolve AVISO, e não prévia vazia --
   prévia vazia seria indistinguível de um dia sem carga.
"""
import csv
import io
import os
import sys
import tempfile
from datetime import date

import carga_litoral
import carga_rj

PASTA = tempfile.mkdtemp(prefix='ensaio_carga_rj_')
DIA = date(2026, 9, 1)

CABECALHO = ['Recurso', 'Data', 'Status da Atividade', 'Nome', 'Endereço',
             'Cidade', 'Intervalo de Tempo', 'Tipo de Atividade',
             'Tipo de Atividade.1', 'Ordem de Serviço', 'Número do contrato',
             'Chave Workzone']

# recurso, cidade escrita no OFS, workzone, turno, tipo, o que se espera
LINHAS = [
    ('RESENDE',    'VOLTA REDONDA', 'RJ-000VRD-WZ003', 'Manhã', 'Reparo Corretivo',   'Volta Redonda'),
    ('RESENDE',    'VOLTA REDONDA', 'RJ-000VRD-WZ003', 'Tarde', 'Ativação',           'Volta Redonda'),
    ('Joao Tecnico RJ', 'BARRA MANSA', 'RJ-000BMA-WZ001', 'Manhã', 'Reparo Corretivo', 'Barra Mansa'),
    ('TRES RIOS',  'TRÊS RIOS',     'RJ-000TRS-WZ002', 'Manhã', 'Mudança de Endereço', 'Três Rios'),
    ('RESENDE',    'RESENDE',       'RJ-000RSD-WZ001', 'Tarde', 'Ativação',           'Resende'),
    # A linha do achado real: cidade escrita CORDEIRO, workzone de Volta
    # Redonda. Quem atende é a rota de Volta Redonda, e é lá que ela conta.
    ('Joao Tecnico RJ', 'CORDEIRO', 'RJ-000VRD-WZ003', 'Manhã', 'Reparo Corretivo',   'Volta Redonda'),
]

caminho = os.path.join(PASTA, 'ofs_rj.csv')
with io.open(caminho, 'w', encoding='utf-8-sig', newline='') as saida:
    escritor = csv.writer(saida)
    escritor.writerow(CABECALHO)
    for i, (recurso, cidade, wz, turno, tipo, _) in enumerate(LINHAS):
        escritor.writerow([recurso, '01/09/26', 'pendente', 'CLIENTE %d' % i,
                           'RUA A, %d CENTRO' % i, cidade, turno, 'Normal',
                           tipo, '800%03d' % i, '71%05d' % i, wz])
    # Ruído que NÃO pode entrar na prévia do Rio.
    escritor.writerow(['RESENDE', '01/09/26', 'cancelado', 'NAO ENTRA CANCELADO',
                       'RUA A, 1 CENTRO', 'RESENDE', 'Manhã', 'Normal',
                       'Ativação', '899', '899', 'RJ-000RSD-WZ001'])
    escritor.writerow(['RESENDE', '01/09/26', 'pendente', 'NAO ENTRA UPGRADE',
                       'RUA A, 1 CENTRO', 'RESENDE', 'Manhã', 'Normal',
                       'Upgrade/Downgrade', '898', '898', 'RJ-000RSD-WZ001'])
    escritor.writerow(['RESENDE', '02/09/26', 'pendente', 'NAO ENTRA OUTRO DIA',
                       'RUA A, 1 CENTRO', 'RESENDE', 'Manhã', 'Normal',
                       'Ativação', '897', '897', 'RJ-000RSD-WZ001'])
    # Litoral: entra na prévia do litoral, nunca na do Rio.
    escritor.writerow(['CARAGUATATUBA', '01/09/26', 'pendente', 'NAO ENTRA LITORAL',
                       'RUA A, 1 CENTRO, CARAGUATATUBA - SP', 'CARAGUATATUBA',
                       'Manhã', 'Normal', 'Ativação', '896', '896',
                       'SP-000CGT-WZ006'])

erros = []


def conferir(rotulo, obtido, esperado):
    if obtido != esperado:
        erros.append('%s: esperava %r, veio %r' % (rotulo, esperado, obtido))
    else:
        print('%-52s %s' % (rotulo, obtido))


rj = carga_litoral.levantar_carga(quando=DIA, caminho=caminho,
                                  recorte=carga_rj.RECORTE_RJ)

conferir('aviso do Rio', rj['aviso'], None)
conferir('nome da regional', rj['regiao_nome'], 'Sul RJ')
conferir('chave da regional', rj['regiao'], 'rj')
conferir('total do Rio (6 entram, 4 são ruído)', rj['total'], 6)

# 1 e 2: cidade pela sigla, com nome -- inclusive a linha do CORDEIRO.
conferir('por cidade', dict(rj['por_cidade']),
         {'Volta Redonda': 3, 'Barra Mansa': 1, 'Três Rios': 1, 'Resende': 1})

# 3: o litoral não vazou para cá, e o Rio não vaza para lá.
if 'CARAGUATATUBA' in rj['por_cidade']:
    erros.append('sigla do litoral entrou na prévia do Rio')
litoral = carga_litoral.levantar_carga(quando=DIA, caminho=caminho)
conferir('prévia do litoral sobre a mesma base', dict(litoral['por_cidade']),
         {'CARAGUATATUBA': 1})

# 5: o balde marca; o técnico não.
conferir('no balde / com técnico', (rj['no_balde'], rj['com_tecnico']), (4, 2))

# 6: base sem a coluna da workzone RECLAMA.
sem_wz = os.path.join(PASTA, 'sem_workzone.csv')
with io.open(sem_wz, 'w', encoding='utf-8-sig', newline='') as saida:
    escritor = csv.writer(saida)
    escritor.writerow(CABECALHO[:-1])
    escritor.writerow(['RESENDE', '01/09/26', 'pendente', 'CLIENTE',
                       'RUA A, 1 CENTRO', 'RESENDE', 'Manhã', 'Normal',
                       'Ativação', '700', '700'])
mudo = carga_litoral.levantar_carga(quando=DIA, caminho=sem_wz,
                                    recorte=carga_rj.RECORTE_RJ)
if not mudo['aviso'] or 'Chave Workzone' not in mudo['aviso']:
    erros.append('base sem Chave Workzone saiu calada: aviso=%r' % mudo['aviso'])
else:
    print('%-52s %s' % ('base sem Chave Workzone avisa', mudo['aviso']))

# A sigla solta, sem passar pela base.
conferir('sigla de VRD', carga_rj.cidade_do_rj('', '', 'RJ-000VRD-WZ003'),
         'Volta Redonda')
conferir('sigla do litoral no recorte do Rio',
         carga_rj.cidade_do_rj('', '', 'SP-000CGT-WZ006'), None)
conferir('workzone vazia', carga_rj.cidade_do_rj('', '', ''), None)

print()
if erros:
    print('ERROS na prévia do Sul RJ:')
    for e in erros:
        print(' -', e)
else:
    print('CONFERE a prévia do Sul RJ: sigla manda, nome da cidade aparece, '
          'litoral e Rio não se misturam')

sys.exit(1 if erros else 0)
