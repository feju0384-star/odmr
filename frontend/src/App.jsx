import { Suspense, lazy } from "react";
import { Center, Loader } from "@mantine/core";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppFrame } from "./components/AppFrame";

const DevicePage = lazy(() => import("./pages/DevicePage"));
const LockinPage = lazy(() => import("./pages/LockinPage"));
const MicrowavePage = lazy(() => import("./pages/MicrowavePage"));
const OdmrPage = lazy(() => import("./pages/OdmrPage"));

export default function App() {
  return (
    <AppFrame>
      <Suspense
        fallback={
          <Center mih="60vh">
            <Loader color="cyan" />
          </Center>
        }
      >
        <Routes>
          <Route path="/" element={<Navigate to="/device" replace />} />
          <Route path="/device" element={<DevicePage />} />
          <Route path="/lockin" element={<LockinPage />} />
          <Route path="/microwave" element={<MicrowavePage />} />
          <Route path="/odmr" element={<OdmrPage />} />
        </Routes>
      </Suspense>
    </AppFrame>
  );
}
