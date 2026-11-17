        private Int32 RunFrame_2(Int32 _container, Int32 _slot, out string _referenceFoil, out string _fyFilter)
        {
            Int32 rt;
            string cmd;
            string cmdStatus;
            _referenceFoil = "";
            _fyFilter = "";

            string[] data;
            string[] tag;
            string[] scanString;

            string error = "";


            string measurementId;
            string sampleId;
            string position;
            string plateType;
            string technique;
            string element;
            string edge;            
            string detection;
            string beam;
            Int32 lastBatch;
            Int32 lastSweep;



            Int32 sweeps;
            
            

            bool isPIPS = false;
            bool isSDD = false;
            bool isFocused = false;

            string trajectoryName;
            string scanName;
            string positionString = "";
            string pathTrajectory;

            double e1, e2;
            double eMidPoint;
            double detuneFactor;

            double scanTime;
            Int32 scanPoints;

            double slit_v_xas;
            double slit_h_xas;
            double slit_v_detune = 4.0;
            double slit_h_detune = 10.0;

            string nextElementEdge;
            string lastElementEdge;

            string nextRefElement;
            string nextRefEdge;
            string lastRefElement;

            string gasMixture = "";

            bool newFrameHasBeenAligned;
            
            StringBuilder sb;


            string nextFyFilter = "";
            string lastFyFilter = "";


            (string Name, double Position) SOL_VERT_FY = ("SOL_VERT", 132.0);
            (string Name, double Position) PIPS_VERT_FY = ("PIPS_VERT", 132.0);
            (string Name, double Position) SOL_VERT_PARKED = ("SOL_VERT", 25.0);
            (string Name, double Position) PIPS_VERT_PARKED = ("PIPS_VERT", 25.0);

            (string Name, double Position) OPTIC_VERT_PARKED = ("OPTIC_VERT", 5.0);
            (string Name, double Position) OPTIC_VERT_PIPE = ("OPTIC_VERT", 73.0);
            (string Name, double Position) OPTIC_VERT_CAP = ("OPTIC_VERT", 148.0);



            #region OPTIC_VERT
            //....................................
            cmd = $"MOTOR MOVE {OPTIC_VERT_PIPE.Name} {OPTIC_VERT_PIPE.Position}";
            cmdStatus = "MOTOR GET_STATUS";
            AddLine($"moving OPTIC_VERT", Color.GhostWhite, FontStyle.Regular);
            rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
            if (rt != CmdUtil.SUCCESS)
            {
                AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                return ERROR_RETURN_SAMPLE_REF;
            }
            //....................................
            #endregion




            #region GET FRAME
            //....................................
            cmd = $"SAMPLE GET_FRAME {_container} {_slot}";
            cmdStatus = "SAMPLE GET_STATUS";
            AddLine($"get frame: ({_container}, {_slot})", Color.GhostWhite, FontStyle.Regular);
            rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
            if (rt != CmdUtil.SUCCESS)
            {
                AddLine($"ERROR: get frame failed", Color.Tomato, FontStyle.Bold);
                return ERROR_HALT;
            }
            //....................................
            #endregion


            #region GET BARCODE
            //....................................            
            cmd = "SCHEDULER GET_BARCODE";
            AddLine($"read tag", Color.GhostWhite, FontStyle.Regular);
            rt = CmdUtil.ExecuteCommand(cmd, out tag, this, __SendFunc, __FinalFunc);
            if (rt != CmdUtil.SUCCESS || tag[0] == "0000-0000" || tag[0] == "000-000")
            {
                AddLine($"ERROR: no tag found", Color.Tomato, FontStyle.Bold);
                return ERROR_RETURN_FRAME;
            }
            else
            {
                AddLine($"tag found: {tag[0]}", Color.GhostWhite, FontStyle.Regular);
            }
            //....................................
            #endregion


            #region GET FRAME INFO
            //....................................
            cmd = $"SCHEDULER GET_FRAME_INFO {tag[0]}";
            rt = CmdUtil.ExecuteCommand(cmd, out scanString, this, __SendFunc, __FinalFunc);
            if (rt != CmdUtil.SUCCESS)
                return ERROR_RETURN_FRAME;
            //....................................
            #endregion

            SortScanString_3(ref scanString);

            #region SEND E-MAIL
            //....................................
            sb = new StringBuilder();
            sb.Append($"SCHEDULER SEND_MESSAGE ");
            sb.Append($"omueller@slac.stanford.edu ");
            sb.Append($"BL22HT_{tag[0]}_NEW_FRAME ");
            sb.Append($"Frame {tag[0]} selected.");
            rt = CmdUtil.ExecuteCommand(sb.ToString(), out data, this, __SendFunc, __FinalFunc);
            //....................................
            #endregion


            // main loop starts here
            lastRefElement = "";
            lastElementEdge = "";
            newFrameHasBeenAligned = false;
            for (Int32 i = 1; i < scanString.Length; i++)
            {
                string[] scanStringParts = scanString[i].Split(" ", StringSplitOptions.RemoveEmptyEntries);


                #region PARSE AND ASSEMBLE IMPORTANT VARIABLES
                //....................................
                measurementId = scanStringParts[OFFSET_SCAN_ID];
                sampleId = scanStringParts[OFFSET_SAMPLE_ID];
                position = scanStringParts[OFFSET_SAMPLE_POSITION].ToUpper();
                
                plateType = scanStringParts[OFFSET_PLATE_TYPE].ToUpper();
                technique = scanStringParts[OFFSET_TECHNIQUE].ToUpper();
                element = scanStringParts[OFFSET_ELEMENT];
                edge = scanStringParts[OFFSET_EDGE];
                detection = scanStringParts[OFFSET_SETUP].ToUpper();
                beam = scanStringParts[OFFSET_BEAM].ToUpper();                
                
                lastBatch = Int32.Parse(scanStringParts[11]);
                lastSweep = Int32.Parse(scanStringParts[12]);



                sweeps = 8;




                nextElementEdge = $"{element}_{edge}";
                trajectoryName = $"{element}_{edge}_{technique}";
                scanName = $"{tag[0]}_{position}";



                isPIPS = detection == "TRANSMISSION_TFY" ? true : false;
                isSDD = detection == "TRANSMISSION_SDD" ? true : false;
                isFocused = beam == "FOCUSED" ? true : false;
                

                ParseApiSamplePosition_2(plateType, position, detection, ref positionString);
                pathTrajectory = $"{PATH_TRAJECTORY}{CRYSTAL_CUT}\\{trajectoryName}.tra";
                //....................................
                #endregion


                #region DEFINE ENERGIES
                //....................................
                //rt = ReadStartStopEnergies_2(pathTrajectory, out e1, out e2, ref error);
                //if (rt != SUCCESS)
                //{
                //    return ERROR_RETURN_FRAME;
                //}
                bool rb;
                double edgeEnergy;
                double xesCenterEnergy = 10000;
                

                XrayEdges.Load("C:\\SSRL_LOCAL_OM\\definitions\\xray_edges.csv");
                rb = XrayEdges.TryGetEnergy(element, edge, out edgeEnergy);
                if (!rb)
                {
                    continue;
                }

                if (isSDD)
                {
                    XrayFluorescence.Load("C:\\SSRL_LOCAL_OM\\definitions\\xray_fluorescence_lines.csv");
                    rb = XrayFluorescence.TryGetEmissionEnergy(element, edge, out xesCenterEnergy);
                    if (!rb)
                    {
                        continue;
                    }
                }
                

                if (technique == "XANES")
                {
                    e1 = edgeEnergy - 200;
                    e2 = edgeEnergy + 300;
                    
                    if (isSDD)
                    {
                        scanPoints = (Int32)(Math.Abs(e2 - e1) / 0.25);     // 2000 points
                        scanTime = Math.Abs(e2 - e1) / 5.0;                 // 100s              50 ms/point
                    }
                    else
                    {
                        scanPoints = (Int32)(Math.Abs(e2 - e1) / 0.20);     // 2500 points
                        scanTime = Math.Abs(e2 - e1) / 10.0;                // 50s              20 ms/point
                    }                    
                }
                else if (technique == "EXAFS")
                {
                    e1 = edgeEnergy - 200;
                    e2 = edgeEnergy + 1000;

                    if (isSDD)
                    {
                        scanPoints = (Int32)(Math.Abs(e2 - e1) / 0.5);      // 2400 points
                        scanTime = Math.Abs(e2 - e1) / 5.0;                 // 240 s            100 ms/points
                    }
                    else
                    {
                        scanPoints = (Int32)(Math.Abs(e2 - e1) / 0.25);     // 4800 points
                        scanTime = Math.Abs(e2 - e1) / 7.5;                 // 160 s            33 ms/point
                    }                    
                }
                else
                {
                    e1 = edgeEnergy - 100;
                    e2 = edgeEnergy + 100;

                    scanPoints = (Int32)(Math.Abs(e2 - e1) / 0.5);                    

                    if (isSDD)
                        scanTime = Math.Abs(e2 - e1) / 10.0;
                    else
                        scanTime = Math.Abs(e2 - e1) / 10.0;
                }

                eMidPoint = (e1 + e2) / 2.0;
                //....................................
                #endregion



                #region DEFINE DETUNE PARAMETERS                
                //....................................
                if (CRYSTAL_CUT == "Si-111")
                {
                    detuneFactor = 0.15;

                    if (eMidPoint < 5000)
                        detuneFactor = 0.45;
                    if (eMidPoint > 10000)
                        detuneFactor = 0.1;
                    else
                        detuneFactor = eMidPoint * -7e-5 + 0.8;
                }
                else if (CRYSTAL_CUT == "Si-220")
                {
                    if (eMidPoint < 12000)
                        detuneFactor = 0.45;
                    if (eMidPoint > 20000)
                        detuneFactor = 0.2;
                    else
                        detuneFactor = eMidPoint * -3.125e-5 + 0.825;
                }
                else
                {
                    detuneFactor = 0.2;
                }
                //....................................
                #endregion



                #region DEFINE BEAM SIZE PARAMETERS
                //....................................
                switch (plateType.ToUpper())
                {
                    case "8-SLOT":
                        slit_h_xas = 10.0;
                        break;

                    case "8-CIRCLE":
                        slit_h_xas = 6.0;
                        break;

                    case "8-CIRCLE-AIR":
                        slit_h_xas = 6.0;
                        break;

                    default:
                        slit_h_xas = 10.0;
                        break;
                }
                slit_v_xas = -4.7e-5 * eMidPoint + 1.43;            // linear mapping: 1.2mm @5keV, 0.5mm @20keV
                slit_v_xas = slit_v_xas < 0.4 ? 0.4 : slit_v_xas;   // 0.4mm absolute minimum
                if (isPIPS || isSDD)
                {
                    slit_h_xas *= Math.Sin(45.0 * Math.PI / 180.0);
                }
                //....................................
                if (isFocused)
                {
                    slit_h_xas = 10.0;
                }
                //....................................
                #endregion



                (nextRefElement, nextRefEdge) = GetSuitableReferenceFoil_2((element, edge));
                nextFyFilter = GetSuitableFyFilter((element, edge), isPIPS);



                if (lastElementEdge != nextElementEdge)         // new element or edge: this requires a full beamline optimization
                {
                    AddLine($"new element or edge: {nextElementEdge}", Color.Orange, FontStyle.Bold);

                    #region RETURN FRAME
                    //....................................
                    cmd = $"SAMPLE RETURN_FRAME {_container} {_slot}";
                    cmdStatus = "SAMPLE GET_STATUS";
                    AddLine($"returning current frame: ({_container}, {_slot}), {tag[0]}", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: returning frame failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_HALT;
                    }
                    //....................................
                    #endregion


                    if (lastRefElement != "")
                    {
                        #region RETURN REFERENCE
                        //....................................
                        cmd = $"SAMPLE RETURN_REF {lastRefElement}";
                        cmdStatus = "SAMPLE GET_STATUS";
                        AddLine($"returning current reference: {lastRefElement}", Color.GhostWhite, FontStyle.Regular);
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        if (rt != CmdUtil.SUCCESS)
                        {
                            AddLine($"ERROR: returning reference failed", Color.Tomato, FontStyle.Bold);
                            return ERROR_HALT;
                        }
                        lastRefElement = "";
                        _referenceFoil = "";
                        //....................................
                        #endregion
                    }

                    if (lastFyFilter != "")
                    {
                        #region RETURN FY FILTER
                        //....................................
                        cmd = $"SAMPLE RETURN_FILTER";
                        cmdStatus = "SAMPLE GET_STATUS";
                        AddLine($"returning current fy filter: {lastFyFilter}", Color.GhostWhite, FontStyle.Regular);
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        if (rt != CmdUtil.SUCCESS)
                        {
                            AddLine($"ERROR: returning fy filter failed", Color.Tomato, FontStyle.Bold);
                            return ERROR_HALT;
                        }
                        lastFyFilter = "";
                        _fyFilter = "";
                        //....................................
                        #endregion
                    }



                    #region GET REFERENCE
                    //....................................
                    cmd = $"SAMPLE GET_REF {nextRefElement}";
                    cmdStatus = "SAMPLE GET_STATUS";
                    AddLine($"getting new reference: {nextRefElement}", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: getting reference failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_HALT;
                    }
                    lastRefElement = nextRefElement;
                    _referenceFoil = nextRefElement;
                    //....................................
                    #endregion



                    if (isPIPS && nextFyFilter != "")
                    {
                        #region GET FY FILTER
                        //....................................
                        cmd = $"SAMPLE GET_FILTER {nextFyFilter}";
                        cmdStatus = "SAMPLE GET_STATUS";
                        AddLine($"getting new reference: {nextFyFilter}", Color.GhostWhite, FontStyle.Regular);
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        if (rt != CmdUtil.SUCCESS)
                        {
                            AddLine($"ERROR: getting fy filter failed", Color.Tomato, FontStyle.Bold);
                            return ERROR_HALT;
                        }
                        lastFyFilter = nextFyFilter;
                        _fyFilter = nextFyFilter;
                        //....................................
                        #endregion
                    }



                    #region GET FRAME
                    //....................................
                    cmd = $"SAMPLE GET_FRAME {_container} {_slot}";
                    cmdStatus = "SAMPLE GET_STATUS";
                    AddLine($"getting frame: ({_container}, {_slot}), {tag[0]}", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: getting frame failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_HALT;
                    }
                    lastElementEdge = nextElementEdge;
                    //....................................
                    #endregion


                    AddLine($"optimizing beamline", Color.Orange, FontStyle.Bold);


                    #region AUTO GAS FILL
                    //....................................                    
                    BuildGasMixerString_2(eMidPoint, out gasMixture);
                    cmd = $"BEAMLINE AUTO_GAS_FILL {gasMixture}";
                    cmdStatus = "BEAMLINE GET_STATUS";
                    AddLine($"filling ion chambers", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: filling ion chambers failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion


                    #region SET SLITS FOR DETUNE
                    //....................................
                    cmd = $"BEAMLINE SET_SLIT_GAP {slit_v_detune} {slit_h_detune}";
                    cmdStatus = "BEAMLINE GET_STATUS";
                    AddLine($"setting slits: {slit_v_detune.ToString("F2")} x {slit_h_detune.ToString("F2")}", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: setting slits failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion
                    #region AUTO DETUNE
                    //....................................
                    cmd = $"BEAMLINE AUTO_DETUNE {eMidPoint} {detuneFactor}";
                    cmdStatus = "BEAMLINE GET_STATUS";
                    AddLine($"detuning mono: {detuneFactor.ToString("F2")} @ {eMidPoint.ToString("F2")}", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: detuning mono failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion


                    #region SET SLITS FOR XAS
                    //....................................
                    cmd = $"BEAMLINE SET_SLIT_GAP {slit_v_xas} {slit_h_xas}";
                    cmdStatus = "BEAMLINE GET_STATUS";
                    AddLine($"setting slits: {slit_v_xas.ToString("F2")} x {slit_h_xas.ToString("F2")}", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: setting slits failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion
                    #region ALIGN TABLE
                    //....................................
                    cmd = "BEAMLINE ALIGN_TABLE";
                    cmdStatus = "BEAMLINE GET_STATUS";
                    AddLine($"aligning table", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: table alignment failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion


                    #region AUTO GAIN
                    //....................................
                    cmd = $"BEAMLINE AUTO_GAIN {e1} {e1 + 215.0} {e2} 0 1 2 3";
                    cmdStatus = "BEAMLINE GET_STATUS";
                    AddLine($"setting amplifier gain @ {e1.ToString("F2")} {(e1 + 215.0).ToString("F2")} {e2.ToString("F2")}", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: auto gain failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion
                    #region CALIBRATE MONO
                    //...............................................
                    cmd = $"SCAN CALIBRATE_ENERGY {nextRefElement} {nextRefEdge} {CRYSTAL_CUT}";
                    cmdStatus = "SCAN GET_STATUS";
                    AddLine($"calibtrating mono: {nextRefElement} | {nextRefEdge} | {CRYSTAL_CUT}", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: mono calibration failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //...............................................
                    #endregion

                }


                if (!newFrameHasBeenAligned)
                {
                    #region PARK SOL_VERT
                    //....................................
                    cmd = $"MOTOR MOVE {SOL_VERT_PARKED.Name} {SOL_VERT_PARKED.Position}";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"parking SOL_VERT", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion


                    #region PARK PIPS_VERT
                    //....................................
                    cmd = $"MOTOR MOVE {PIPS_VERT_PARKED.Name} {PIPS_VERT_PARKED.Position}";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"parking SOL_VERT", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion


                    #region PARK OPTIC_VERT
                    //....................................
                    cmd = $"MOTOR MOVE {OPTIC_VERT_PARKED.Name} {OPTIC_VERT_PARKED.Position}";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"parking OPTIC_VERT", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion


                    #region ALIGN FRAME
                    // reduce mono energy, at >10keV the frame itself becomes too transparent to reliably detect the frame's edges
                    //....................................                    
                    cmd = "SCAN GET_MONO_ENERGY";
                    rt = CmdUtil.ExecuteCommand(cmd, out data, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: getting current mono energy failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    double monoEnergyBeforeFrameAlignment = double.Parse(data[0]);
                    //....................................

                    if (monoEnergyBeforeFrameAlignment > 13000)
                    {
                        //....................................
                        cmd = "BEAMLINE MOVE_MONO 10000";
                        cmdStatus = "BEAMLINE GET_STATUS";
                        AddLine($"move Mono 10 keV", Color.GhostWhite, FontStyle.Regular);
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        if (rt != CmdUtil.SUCCESS)
                        {
                            AddLine($"ERROR: move mono failed", Color.Tomato, FontStyle.Bold);
                            return ERROR_RETURN_SAMPLE_REF;
                        }
                        //....................................
                    }
                    
                    //....................................
                    cmd = "SAMPLE CALIBRATE_FRAME";
                    cmdStatus = "SAMPLE GET_STATUS";
                    AddLine($"aligning frame", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: aligning sample failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    
                    
                    if (monoEnergyBeforeFrameAlignment > 13000)
                    {
                        //....................................
                        cmd = $"BEAMLINE MOVE_MONO {monoEnergyBeforeFrameAlignment}";
                        cmdStatus = "BEAMLINE GET_STATUS";
                        AddLine($"move Mono", Color.GhostWhite, FontStyle.Regular);
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        if (rt != CmdUtil.SUCCESS)
                        {
                            AddLine($"ERROR: move mono failed", Color.Tomato, FontStyle.Bold);
                            return ERROR_RETURN_SAMPLE_REF;
                        }
                        //....................................
                    }

                    newFrameHasBeenAligned = true;
                    #endregion
                }


                #region PARK SOL_VERT
                //....................................
                cmd = $"MOTOR MOVE {SOL_VERT_PARKED.Name} {SOL_VERT_PARKED.Position}";
                cmdStatus = "MOTOR GET_STATUS";
                AddLine($"parking SOL_VERT", Color.GhostWhite, FontStyle.Regular);
                rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                if (rt != CmdUtil.SUCCESS)
                {
                    AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                    return ERROR_RETURN_SAMPLE_REF;
                }
                //....................................
                #endregion

                #region PLACE SAMPLE
                //....................................
                cmd = $"SAMPLE PLACE {positionString}";
                cmdStatus = "SAMPLE GET_STATUS";
                AddLine($"placing sample: {positionString} {tag[0]}", Color.Orange, FontStyle.Bold);
                rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                if (rt != CmdUtil.SUCCESS)
                {
                    AddLine($"ERROR: placing sample failed", Color.Tomato, FontStyle.Bold);
                    return ERROR_RETURN_SAMPLE_REF;
                }
                //....................................
                #endregion


                if (!isSDD && isPIPS && nextFyFilter != "")
                {
                    #region RESTORE SOL_VERT
                    //....................................
                    cmd = $"MOTOR MOVE {SOL_VERT_FY.Name} {SOL_VERT_FY.Position}";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"moving SOL_VERT", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion
                }
                if (!isSDD && isPIPS)
                {
                    #region RESTORE PIPS_VERT
                    //....................................
                    cmd = $"MOTOR MOVE {PIPS_VERT_FY.Name} {PIPS_VERT_FY.Position}";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"moving PIPS_VERT", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion
                }








              

                #region PARK OPTIC_VERT
                //....................................
                cmd = $"MOTOR MOVE {OPTIC_VERT_PARKED.Name} {OPTIC_VERT_PARKED.Position}";
                cmdStatus = "MOTOR GET_STATUS";
                AddLine($"parking OPTIC_VERT", Color.GhostWhite, FontStyle.Regular);
                rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                if (rt != CmdUtil.SUCCESS)
                {
                    AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                    return ERROR_RETURN_SAMPLE_REF;
                }
                //....................................
                #endregion


                //....................................
                cmd = $"SCHEDULER GET_PHOTO {scanName}";
                AddLine($"getting sample photo", Color.Orange, FontStyle.Bold);
                rt = CmdUtil.ExecuteCommand(cmd, out data, this, __SendFunc, __FinalFunc);
                if (rt != CmdUtil.SUCCESS)
                {
                    AddLine($"ERROR: getting sample photo failed", Color.Tomato, FontStyle.Bold);
                    return ERROR_RETURN_SAMPLE_REF;
                }
                //....................................


                #region RESTORE OPTIC_VERT
                //....................................
                if(isFocused)
                    cmd = $"MOTOR MOVE {OPTIC_VERT_CAP.Name} {OPTIC_VERT_CAP.Position}";
                else
                    cmd = $"MOTOR MOVE {OPTIC_VERT_PIPE.Name} {OPTIC_VERT_PIPE.Position}";

                cmdStatus = "MOTOR GET_STATUS";
                AddLine($"restoring OPIC_VERT", Color.GhostWhite, FontStyle.Regular);
                rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                if (rt != CmdUtil.SUCCESS)
                {
                    AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                    return ERROR_RETURN_SAMPLE_REF;
                }
                //....................................
                #endregion
                


                #region SET SLITS FOR XAS
                //....................................
                cmd = $"BEAMLINE SET_SLIT_GAP {slit_v_xas} {slit_h_xas}";
                cmdStatus = "BEAMLINE GET_STATUS";
                AddLine($"setting slits: {slit_v_xas.ToString("F2")} x {slit_h_xas.ToString("F2")}", Color.GhostWhite, FontStyle.Regular);
                rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                if (rt != CmdUtil.SUCCESS)
                {
                    AddLine($"ERROR: setting slits failed", Color.Tomato, FontStyle.Bold);
                    return ERROR_RETURN_SAMPLE_REF;
                }
                //....................................
                #endregion


                #region AUTO GAIN
                //....................................
                cmd = $"BEAMLINE AUTO_GAIN {e1} {e1 + 215.0} {e2} 0 1 2 3";
                cmdStatus = "BEAMLINE GET_STATUS";
                AddLine($"setting amplifier gain @ {e1.ToString("F2")} {(e1 + 215.0).ToString("F2")} {e2.ToString("F2")} ", Color.GhostWhite, FontStyle.Regular);
                rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                if (rt != CmdUtil.SUCCESS)
                {
                    AddLine($"ERROR: auto gain failed", Color.Tomato, FontStyle.Bold);
                    return ERROR_RETURN_SAMPLE_REF;
                }
                //....................................
                #endregion


                #region COLLECTING OFFSETS
                //....................................
                cmd = $"BEAMLINE COLLECT_OFFSETS";
                cmdStatus = "BEAMLINE GET_STATUS";
                AddLine($"collecting offsets", Color.GhostWhite, FontStyle.Regular);
                rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                if (rt != SUCCESS)
                {
                    AddLine($"ERROR: collecting offsets failed", Color.Tomato, FontStyle.Bold);
                    return ERROR_RETURN_SAMPLE_REF;
                }
                //....................................
                #endregion


                #region GET BEAMLINE STATUS
                //....................................                
                sb = new StringBuilder();

                sb.Append($"Scan:\t{scanName}\r\n");

                sb.Append($"Trajectory:\t{trajectoryName}\r\n");

                sb.Append($"Reference:\t{_referenceFoil}\r\n");

                
                sb.Append($"Mode:\t{detection.ToUpper()}\r\n");
                    
                if(_fyFilter != "")
                    sb.Append($"FY_FILTER:\t{_fyFilter}\r\n");
                else                    
                    sb.Append($"FY_FILTER:\tN/A\r\n");               

                rt = CmdUtil.ExecuteCommand("BEAMLINE GET_GAIN", out data, this, __SendFunc, __FinalFunc);
                sb.Append($"AMP_GAIN:\t{data[0]}\r\n");

                rt = CmdUtil.ExecuteCommand("BEAMLINE GET_SLIT_GAP", out data, this, __SendFunc, __FinalFunc);
                sb.Append($"SLIT_GAP:\t{data[0]}\r\n");

                rt = CmdUtil.ExecuteCommand("BEAMLINE GET_SLIT_CENTER", out data, this, __SendFunc, __FinalFunc);
                sb.Append($"SLIT_CENTER:\t{data[0]}\r\n");

                sb.Append($"DETUNE:\t{detuneFactor.ToString("F3")}\t{eMidPoint.ToString("F3")}eV\r\n");

                sb.Append($"IC_GAS:\t{gasMixture}\r\n");


                try
                {
                    Int32 scanInfoFilenameCounter;
                    string scanInfoFilename;

                    scanInfoFilenameCounter = 0;
                    do
                    {
                        scanInfoFilenameCounter++;
                        scanInfoFilename = $"C:\\SSRL_LOCAL_OM\\ScanInfo\\{scanName}_{trajectoryName}_" + scanInfoFilenameCounter.ToString("D2") + ".txt";
                    }
                    while (File.Exists(scanInfoFilename));

                    FileStream fs = new FileStream(scanInfoFilename, FileMode.Create);
                    StreamWriter writer = new StreamWriter(fs);
                    writer.Write(sb.ToString());
                    writer.Close();
                    fs.Close();
                }
                catch (Exception ex)
                {

                }
                //....................................                
                #endregion


                    

                //~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                //~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                if (isSDD)
                {
                    #region PARK SOL_VERT
                    //....................................
                    cmd = $"MOTOR MOVE {SOL_VERT_PARKED.Name} {SOL_VERT_PARKED.Position}";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"parking SOL_VERT", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion

                    #region PARK PIPS_VERT
                    //....................................
                    cmd = $"MOTOR MOVE {PIPS_VERT_PARKED.Name} {PIPS_VERT_PARKED.Position}";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"parking PIPS_VERT", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion


                    #region MOVING DET_X
                    //....................................
                    cmd = $"MOTOR MOVE DET_X 200";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"moving DET_X 200", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion

                    #region MOVING DET_Y
                    //....................................
                    cmd = $"MOTOR MOVE DET_Y 10";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"moving DET_Y 10", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion
                }
                //~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                //~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~



                //################
                #region START XAS SCAN                
                if (isSDD)
                {
                    Int32 roi1Size = 40;
                    Int32 roi2Size = 80;

                    Int32 roi1Min = (Int32)Math.Floor(xesCenterEnergy / 10 - roi1Size/2.0);
                    Int32 roi2Min = (Int32)Math.Floor(xesCenterEnergy / 10 - roi2Size / 2.0);
                 
                    //....................................                
                    cmd = $"SCAN START_XAS_FY_SCAN {scanName} {e1} {e2} {scanTime} {scanPoints} {sweeps} {roi1Min} {roi1Size} {roi2Min} {roi2Size}";
                    cmdStatus = "SCAN GET_STATUS";
                    AddLine($"XAS FY SCAN: {scanName}, {trajectoryName}", Color.Orange, FontStyle.Bold);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, out data, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                        return ERROR_RETURN_SDD_SAMPLE_REF; ;
                    //....................................
                }
                else
                {
                    //....................................                
                    //cmd = $"SCAN START_XAS_SCAN {scanName} {trajectoryName} {sweeps} {sampleId} {scanId}";                    
                    cmd = $"SCAN START_XAS_SCAN {scanName} {e1} {e2} {scanTime} {scanPoints} {sweeps}";
                    cmdStatus = "SCAN GET_STATUS";
                    AddLine($"XAS SCAN: {scanName}, {trajectoryName}", Color.Orange, FontStyle.Bold);                    
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, out data, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                        return ERROR_RETURN_SAMPLE_REF;
                    //....................................
                }
                #endregion
                //################

                string name = data[0];
                if (name.Contains(" "))
                    name = name.Trim();
                for (Int32 n = 0; n < sweeps; n++)
                {
                    if(isSDD)
                        cmd = $"SCAN POST_XAS_FY_SCAN {name} {n+1} {measurementId} {lastSweep + 1 + n} {lastBatch + 1} {technique} {element} {edge} {detection} {beam}";
                    else
                        cmd = $"SCAN POST_XAS_SCAN {name} {n + 1} {measurementId} {lastSweep + 1 + n} {lastBatch + 1} {technique} {element} {edge} {detection} {beam}";
                    rt = CmdUtil.ExecuteCommand(cmd, out data, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                        continue;
                }






                //~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                //~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                if (isSDD)
                {
                    #region MOVING DET_Y
                    //....................................
                    cmd = $"MOTOR MOVE DET_Y 130";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"parking DET_Y 130", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion


                    #region MOVING DET_X
                    //....................................
                    cmd = $"MOTOR MOVE DET_X 0";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"parking DET_X 0", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion
                }
                //~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
                //~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~




                if (isFocused)
                {
                    #region OPTIC_VERT
                    //....................................
                    cmd = $"MOTOR MOVE {OPTIC_VERT_PIPE.Name} {OPTIC_VERT_PIPE.Position}";
                    cmdStatus = "MOTOR GET_STATUS";
                    AddLine($"moving OPTIC_VERT", Color.GhostWhite, FontStyle.Regular);
                    rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                    if (rt != CmdUtil.SUCCESS)
                    {
                        AddLine($"ERROR: moving motor failed", Color.Tomato, FontStyle.Bold);
                        return ERROR_RETURN_SAMPLE_REF;
                    }
                    //....................................
                    #endregion
                }






                #region SEND E-MAIL
                //....................................
                sb = new StringBuilder();
                sb.Append($"SCHEDULER SEND_MESSAGE ");
                sb.Append($"omueller@slac.stanford.edu ");
                sb.Append($"BL22HT_{tag[0]}_XAS_SCAN ");
                sb.Append($"{trajectoryName} scan of sample {positionString} {tag[0]} complete.");
                rt = CmdUtil.ExecuteCommand(sb.ToString(), out data, this, __SendFunc, __FinalFunc);
                if (rt != CmdUtil.SUCCESS)
                    return ERROR_RETURN_SAMPLE_REF;
                //....................................
                #endregion
            }


            #region RETURN FRAME
            //....................................
            cmd = $"SAMPLE RETURN_FRAME {_container} {_slot}";
            cmdStatus = "SAMPLE GET_STATUS";
            AddLine($"returning current frame: ({_container}, {_slot}), {tag[0]}", Color.GhostWhite, FontStyle.Regular);
            rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
            if (rt != CmdUtil.SUCCESS)
            {
                AddLine($"ERROR: returning frame failed", Color.Tomato, FontStyle.Bold);
                return ERROR_HALT;
            }
            //....................................
            #endregion


            #region RETURN REFERENCE
            //....................................                    
            cmd = $"SAMPLE RETURN_REF {lastRefElement}";
            cmdStatus = "SAMPLE GET_STATUS";
            AddLine($"returning current reference: {lastRefElement}", Color.GhostWhite, FontStyle.Regular);
            rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
            if (rt != CmdUtil.SUCCESS)
            {
                AddLine($"ERROR: returning reference failed", Color.Tomato, FontStyle.Bold);
                return ERROR_HALT;
            }
            lastRefElement = "";
            _referenceFoil = "";
            //....................................
            #endregion


            if (lastFyFilter != "")
            {
                #region RETURN FY FILTER
                //....................................
                cmd = $"SAMPLE RETURN_FILTER";
                cmdStatus = "SAMPLE GET_STATUS";
                AddLine($"returning current fy filter: {lastFyFilter}", Color.GhostWhite, FontStyle.Regular);
                rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                if (rt != CmdUtil.SUCCESS)
                {
                    AddLine($"ERROR: returning fy filter failed", Color.Tomato, FontStyle.Bold);
                    return ERROR_HALT;
                }
                lastFyFilter = "";
                _fyFilter = "";
                //....................................
                #endregion
            }




            #region SEND E-MAIL
            //....................................
            sb = new StringBuilder();
            sb.Append($"SCHEDULER SEND_MESSAGE ");
            sb.Append($"omueller@slac.stanford.edu ");
            sb.Append($"BL22HT_{tag[0]}_Frame_Complete ");
            sb.Append($"Frame {tag[0]} has been completed.");
            rt = CmdUtil.ExecuteCommand(sb.ToString(), out data, this, __SendFunc, __FinalFunc);
            if (rt != CmdUtil.SUCCESS)
                return ERROR;
            //....................................
            #endregion



            AddLine($"RUN FRAME COMPLETE: ({_container}, {_slot}), {tag[0]}", Color.CadetBlue, FontStyle.Bold);

            return SUCCESS;
        }


        private void button1_Click(object sender, EventArgs e)
        {
            Int32 rt;
            string cmd;
            string cmdStatus;
            string referenceFoil;
            string fyFilter;

            
            List<(Int32 containerID, Int32 slotID)> sampleFrameList = new List<(Int32, Int32)>();
            
            sampleFrameList.Add((0, 9));
            sampleFrameList.Add((0, 6));
            sampleFrameList.Add((0, 5));


            for (Int32 frame = 0; frame < sampleFrameList.Count; frame++)
            {
                rt = RunFrame_2(sampleFrameList[frame].containerID, sampleFrameList[frame].slotID, out referenceFoil, out fyFilter);

                switch (rt)
                {
                    case ERROR_RETURN_FRAME:
                        cmd = $"SAMPLE RETURN_FRAME {sampleFrameList[frame].containerID} {sampleFrameList[frame].slotID}";
                        cmdStatus = "SAMPLE GET_STATUS";
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        break;


                    case ERROR_RETURN_REF:
                        cmd = $"SAMPLE RETURN_REF {referenceFoil}";
                        cmdStatus = "SAMPLE GET_STATUS";
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        break;


                    case ERROR_RETURN_SAMPLE_REF:
                        cmd = $"SAMPLE RETURN_FRAME {sampleFrameList[frame].containerID} {sampleFrameList[frame].slotID}";
                        cmdStatus = "SAMPLE GET_STATUS";
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);

                        cmd = $"SAMPLE RETURN_REF {referenceFoil}";
                        cmdStatus = "SAMPLE GET_STATUS";
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        break;

                    case ERROR_RETURN_SDD_SAMPLE_REF:
                        cmd = $"MOTOR MOVE DET_Y 130";
                        cmdStatus = "MOTOR GET_STATUS";                        
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        if (rt != CmdUtil.SUCCESS)
                        {
                            return;
                        }

                        cmd = $"MOTOR MOVE DET_X 0";
                        cmdStatus = "MOTOR GET_STATUS";
                        AddLine($"parking DET_X 0", Color.GhostWhite, FontStyle.Regular);
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        if (rt != CmdUtil.SUCCESS)
                        {
                            return;
                        }
                        
                        cmd = $"SAMPLE RETURN_FRAME {sampleFrameList[frame].containerID} {sampleFrameList[frame].slotID}";
                        cmdStatus = "SAMPLE GET_STATUS";
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);

                        cmd = $"SAMPLE RETURN_REF {referenceFoil}";
                        cmdStatus = "SAMPLE GET_STATUS";
                        rt = CmdUtil.ExecuteCommandBlocking(cmd, cmdStatus, this, __SendFunc, __FinalFunc);
                        break;

                    default:
                        break;
                }

            }
        }
