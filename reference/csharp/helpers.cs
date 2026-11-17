        private (string Element, string Edge) GetSuitableReferenceFoil_2((string Element, string Edge) _xas)
        {
            (string Element, string Edge) refFoil;

            switch (_xas.Element)
            {
                
                
                
                // 56
                case "Ba":
                    refFoil.Element = "V";
                    refFoil.Edge = "K";

                    if (_xas.Edge == "L-1")
                    {
                        refFoil.Element = "Cr";
                        refFoil.Edge = "K";
                    }
                    break;

                // 83
                case "Bi":
                    refFoil.Element = "Pt";
                    refFoil.Edge = "L-3";
                    break;

                // 58
                case "Ce":
                    refFoil.Element = "Cr";
                    refFoil.Edge = "K";
                    break;

                // 31
                case "Ga":
                    refFoil.Element = "W";
                    refFoil.Edge = "L-3";
                    break;

                // 64
                case "Gd":
                    refFoil.Element = "Fe";
                    refFoil.Edge = "K";
                    if (_xas.Edge == "L-1")
                    {
                        refFoil.Element = "Ni";
                        refFoil.Edge = "K";
                    }
                    else if (_xas.Edge == "L-2")
                    {
                        refFoil.Element = "Co";
                        refFoil.Edge = "K";
                    }
                    break;

                // 77
                case "Ir":
                    refFoil.Element = "Pt";
                    refFoil.Edge = "L-3";
                    break;

                // 57
                case "La":
                    refFoil.Element = "Cr";
                    refFoil.Edge = "K";
                    break;

                // 60
                case "Nd":
                    refFoil.Element = "Mn";
                    refFoil.Edge = "K";
                    if (_xas.Edge == "L-1")
                    {
                        refFoil.Element = "Fe";
                        refFoil.Edge = "K";
                    }
                    else if (_xas.Edge == "L-2")
                    {
                        refFoil.Element = "Mn";
                        refFoil.Edge = "K";
                    }
                    break;

                // 59
                case "Pr":
                    refFoil.Element = "Cr";
                    refFoil.Edge = "K";
                    if (_xas.Edge == "L-1")
                    {
                        refFoil.Element = "Fe";
                        refFoil.Edge = "K";
                    }
                    else if (_xas.Edge == "L-2")
                    {
                        refFoil.Element = "Mn";
                        refFoil.Edge = "K";
                    }
                    break;

                // 62
                case "Sm":
                    refFoil.Element = "Fe";
                    refFoil.Edge = "K";                    
                    break;

                // 66
                case "Dy":
                    refFoil.Element = "Co";
                    refFoil.Edge = "K";
                    if (_xas.Edge == "L-1")
                    {
                        refFoil.Element = "Cu";
                        refFoil.Edge = "K";
                    }
                    else if (_xas.Edge == "L-2")
                    {
                        refFoil.Element = "Cu";
                        refFoil.Edge = "K";
                    }
                    break;

                // 70
                case "Yb":
                    refFoil.Element = "Cu";
                    refFoil.Edge = "K";
                    if (_xas.Edge == "L-1")
                    {
                        refFoil.Element = "W";
                        refFoil.Edge = "L-3";
                    }
                    else if (_xas.Edge == "L-2")
                    {
                        refFoil.Element = "Ta";
                        refFoil.Edge = "L-3";
                    }
                    break;

                // 38
                case "Sr":
                    refFoil.Element = "Zr";
                    refFoil.Edge = "K";
                    break;

                default:
                    refFoil.Element = _xas.Element;
                    refFoil.Edge = _xas.Edge;
                    break;
            }

            return refFoil;
        }


        private string GetSuitableFyFilter((string Element, string Edge) _xas, bool isFy)
        {
            string fyFilter = "";


            if (!isFy)
                return fyFilter;

            switch (_xas.Element)
            {
                case "V":
                    fyFilter = "TI-3";
                    break;

                case "Cr":
                    fyFilter = "V-3";
                    break;

                case "Mn":
                    fyFilter = "";
                    break;

                case "Fe":
                    fyFilter = "MN-3";
                    break;

                case "Co":
                    fyFilter = "FE-3";
                    break;

                case "Ni":
                    fyFilter = "CO-3";
                    break;

                case "Cu":
                    fyFilter = "NI-3";
                    break;

                case "Zn":
                    fyFilter = "CU-3";
                    break;

                case "Ga":
                    fyFilter = "ZN-3";
                    break;

                case "Ge":
                    fyFilter = "GA-3";
                    break;

                //case "As":
                //    fyFilter = "GE-3";
                //    break;

                //case "Br":
                //    fyFilter = "SE-3";
                //    break;

                case "Nb":
                    fyFilter = "ZR-3";
                    break;

                case "Mo":
                    fyFilter = "NB-3";
                    break;

                default:
                    fyFilter = "";
                    break;
            }

            return fyFilter;
        }

        private void BuildGasMixerString_2(double midPoint, out string mix)
        {
            string ic0_gas1, ic0_gas2, ic0_p1, ic0_p2;
            string ic1_gas1, ic1_gas2, ic1_p1, ic1_p2;
            string ic2_gas1, ic2_gas2, ic2_p1, ic2_p2;


            if (midPoint < 7000)
            {
                ic0_gas1 = "NITROGEN";
                ic0_p1 = "1.1";
                ic0_gas2 = "NITROGEN";
                ic0_p2 = "0";

                ic1_gas1 = "NITROGEN";
                ic1_p1 = "1.6";
                //ic1_p1 = "1.8";
                ic1_gas2 = "NITROGEN";
                ic1_p2 = "0";

                ic2_gas1 = "NITROGEN";
                ic2_p1 = "2.1";
                ic2_gas2 = "NITROGEN";
                ic2_p2 = "0";
            }
            else if (midPoint >= 7000 && midPoint < 12000)
            {
                ic0_gas1 = "NITROGEN";
                ic0_p1 = "1.5";
                ic0_gas2 = "NITROGEN";
                ic0_p2 = "0";

                ic1_gas1 = "NITROGEN";
                //ic1_p1 = "2.0";
                ic1_p1 = "2.5";
                ic1_gas2 = "NITROGEN";
                ic1_p2 = "0";

                ic2_gas1 = "NITROGEN";
                ic2_p1 = "3.0";
                ic2_gas2 = "NITROGEN";
                ic2_p2 = "0";
            }
            else if (midPoint >= 12000 && midPoint < 18000)
            {
                ic0_gas1 = "NITROGEN";
                ic0_p1 = "3.0";
                ic0_gas2 = "NITROGEN";
                ic0_p2 = "0";

                ic1_gas1 = "ARGON";
                ic1_p1 = "1.0";
                ic1_gas2 = "NITROGEN";
                ic1_p2 = "2.0";

                ic2_gas1 = "ARGON";
                ic2_p1 = "1.0";
                ic2_gas2 = "NITROGEN";
                ic2_p2 = "2.0";
            }
            else if (midPoint >= 18000 && midPoint < 32000)
            {
                ic0_gas1 = "ARGON";
                ic0_p1 = "1.0";
                ic0_gas2 = "NITROGEN";
                ic0_p2 = "2.0";

                ic1_gas1 = "NITROGEN";
                ic1_p1 = "1.0";
                ic1_gas2 = "ARGON";
                ic1_p2 = "2.0";

                ic2_gas1 = "NITROGEN";
                ic2_p1 = "1.0";
                ic2_gas2 = "ARGON";
                ic2_p2 = "2.0";
            }
            else
            {
                ic0_gas1 = "NITROGEN";
                ic0_p1 = "1.0";
                ic0_gas2 = "ARGON";
                ic0_p2 = "2.0";

                ic1_gas1 = "NITROGEN";
                ic1_p1 = "1.0";
                ic1_gas2 = "ARGON";
                ic1_p2 = "2.0";

                ic2_gas1 = "NITROGEN";
                ic2_p1 = "1.0";
                ic2_gas2 = "ARGON";
                ic2_p2 = "2.0";
            }

            mix = $"{ic0_gas1} {ic0_p1} {ic0_gas2} {ic0_p2} " +
                  $"{ic1_gas1} {ic1_p1} {ic1_gas2} {ic1_p2} " +
                  $"{ic2_gas1} {ic2_p1} {ic2_gas2} {ic2_p2}";


        }



        private void ParseApiSamplePosition_2(string apiPlateType, string apiPostionString, string apiSetupString, ref string samplePosition)
        {
            string plateType = "";
            string platePosition = "";
            string isFy = "";
            string[] apiPositionStringParts;

            Dictionary<string, Int32> samplePositionMap = new Dictionary<string, Int32>();
            samplePositionMap.Add("A", 1);
            samplePositionMap.Add("B", 2);
            samplePositionMap.Add("C", 3);
            samplePositionMap.Add("D", 4);
            samplePositionMap.Add("E", 5);
            samplePositionMap.Add("F", 6);
            samplePositionMap.Add("G", 7);
            samplePositionMap.Add("H", 8);


            plateType = apiPlateType;

            apiPositionStringParts = apiPostionString.Split("-");
            switch (apiPositionStringParts[0].ToUpper())
            {
                case "LEFT":
                    platePosition = apiPlateType.ToUpper() == "8-CIRCLE-AIR" ? "NONE" : "LEFT";
                    break;

                case "RIGHT":
                    platePosition = "RIGHT";
                    break;

                default:
                    break;
            }

            switch (apiSetupString.ToUpper())
            {
                case "TRANSMISSION":
                    isFy = "FALSE";
                    break;

                case "TRANSMISSION_TFY":
                    isFy = "TRUE";
                    break;

                case "TRANSMISSION_SDD":
                    isFy = "TRUE";
                    break;

                default:
                    break;
            }
           
            samplePosition = $"{plateType} {platePosition} {samplePositionMap[apiPositionStringParts[1]]} {isFy}";
        }