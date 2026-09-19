import { useSelection } from "@canva/app-hooks";
import { Button, Rows, Text, Box } from "@canva/app-ui-kit";
import { requestExport } from "@canva/design";
import { upload } from "@canva/asset";
import React, { useEffect, useState } from "react";
import * as styles from "styles/components.css";

export const App = () => {
  const videoSelection = useSelection("video");
  const [selectedRef, setSelectedRef] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string>("Aguardando seleção...");
  const [isProcessing, setIsProcessing] = useState<boolean>(false);

  // Detect and capture user selection in Canva editor
  useEffect(() => {
    async function handleSelection() {
      if (videoSelection.count > 0) {
        try {
          const draft = await videoSelection.read();
          if (draft.contents && draft.contents.length > 0) {
            const ref = draft.contents[0].ref;
            setSelectedRef(ref);
            setStatusMessage(`Vídeo selecionado! Ref: ${ref}`);

            // Send message to Playwright / parent window
            window.parent.postMessage(
              {
                type: "CLIPFORGE_PLACEHOLDER_SELECTED",
                ref,
                count: videoSelection.count,
                timestamp: Date.now(),
              },
              "*"
            );
          }
        } catch (err: any) {
          console.error("Erro ao ler seleção de vídeo:", err);
        }
      }
    }
    handleSelection();
  }, [videoSelection]);

  // Listen for render commands from ClipForge backend / Playwright
  useEffect(() => {
    const messageHandler = async (event: MessageEvent) => {
      const data = event.data;
      if (!data || typeof data !== "object") return;

      if (data.type === "CLIPFORGE_START_RENDER") {
        setIsProcessing(true);
        setStatusMessage("Iniciando render...");

        try {
          const { videoUrl } = data;
          if (!videoUrl) {
            throw new Error("videoUrl não informado no payload");
          }

          setStatusMessage("Fazendo upload do vídeo clipe...");
          const asset = await upload({
            type: "video",
            url: videoUrl,
            mimeType: "video/mp4",
          });
          await asset.whenUploaded();

          setStatusMessage("Substituindo vídeo no elemento...");
          if (videoSelection.count > 0) {
            const draft = await videoSelection.read();
            if (draft.contents && draft.contents.length > 0) {
              draft.contents[0].ref = asset.ref;
              await draft.save();
            }
          }

          setStatusMessage("Disparando exportação no Canva...");
          const exportResponse = await requestExport({
            acceptedFileTypes: ["video"],
          });

          setStatusMessage("Render concluído!");
          window.parent.postMessage(
            {
              type: "CLIPFORGE_RENDER_COMPLETED",
              response: exportResponse,
              timestamp: Date.now(),
            },
            "*"
          );
        } catch (err: any) {
          console.error("Erro no processamento de render:", err);
          setStatusMessage(`Erro: ${err.message || String(err)}`);
          window.parent.postMessage(
            {
              type: "CLIPFORGE_RENDER_FAILED",
              error: err.message || String(err),
              timestamp: Date.now(),
            },
            "*"
          );
        } finally {
          setIsProcessing(false);
        }
      }
    };

    window.addEventListener("message", messageHandler);
    return () => window.removeEventListener("message", messageHandler);
  }, [videoSelection]);

  const handleManualMap = async () => {
    if (videoSelection.count === 0) {
      setStatusMessage("Nenhum vídeo selecionado no Canva. Clique em um elemento de vídeo primeiro!");
      return;
    }
    try {
      const draft = await videoSelection.read();
      if (draft.contents && draft.contents.length > 0) {
        const ref = draft.contents[0].ref;
        setSelectedRef(ref);
        setStatusMessage(`Mapeado manualmente! Ref: ${ref}`);
        window.parent.postMessage(
          {
            type: "CLIPFORGE_PLACEHOLDER_SELECTED",
            ref,
            timestamp: Date.now(),
          },
          "*"
        );
      }
    } catch (err: any) {
      setStatusMessage(`Falha ao mapear: ${err.message}`);
    }
  };

  return (
    <div className={styles.scrollContainer}>
      <Rows spacing="2u">
        <Text tone="neutral">
          <strong>ClipForge Engine</strong>
        </Text>
        <Text>
          {statusMessage}
        </Text>
        {selectedRef && (
          <Box background="neutralLow" padding="1u">
            <Text tone="neutral">
              <code>{selectedRef}</code>
            </Text>
          </Box>
        )}
        <Button
          variant="primary"
          onClick={handleManualMap}
          disabled={videoSelection.count === 0 || isProcessing}
          stretch
        >
          Confirmar Slot de Vídeo
        </Button>
      </Rows>
    </div>
  );
};
