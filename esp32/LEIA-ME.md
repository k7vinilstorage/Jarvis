# ESP32 (Fase 3)

`rascunho-antigo/` é um esboço feito antes da ponte de voz atual. Ele fala **HTTP** com uma versão da ponte que não existe mais e **nunca foi revisado nem testado**. Serve só de referência para as ligações (INMP441, DAC) e para o código de I2S.

A Fase 3 vai refazer o firmware com o protocolo WebSocket do `jarvis-voz` (veja `docs/fase2.md`):
- botão para falar;
- microfone I2S a 16 kHz;
- DAC com `saida_formato u8`;
- LED pelos estados.

Nunca coloque as credenciais reais de WiFi em arquivos versionados. Use `SUA_REDE` e `SUA_SENHA` e um `segredos.h` fora do git.
