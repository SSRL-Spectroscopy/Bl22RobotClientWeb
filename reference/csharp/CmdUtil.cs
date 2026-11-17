using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace RobotClientBL22HT
{
    public class CmdUtil
    {

        public static Int32 SUCCESS = 1;
        public static Int32 ERROR = -1;
        public static Int32 POLL_TIME_MS = 500;
        public static Int32 DELAY_SHORT = 100;


        public delegate Int32 SendFunc(object _sender, ref string _req, ref string _res);
        public delegate void FinalFunc(object _sender, ref string _reg, ref string _res);


        public static Int32 ExecuteCommand(string _cmd, out string[] _data, object _sender, SendFunc _SendFunc, FinalFunc _FinalFunc)
        {
            string[] resArr;
            string[] resSplit;
            
            string res = "";
            string req = _cmd;


            _data = new string[1];
            _data[0] = "";

            //.........................................
            _SendFunc(_sender, ref req, ref res);
            _FinalFunc(_sender, ref req, ref res);
            //.........................................
            resArr = res.Split(" ", StringSplitOptions.RemoveEmptyEntries);
            if (resArr[0] != "SUCCESS")
                return ERROR;

            resSplit = res.Split('\r', StringSplitOptions.RemoveEmptyEntries);
            _data = new string[resSplit.Length - 2];            // remove first and last part
            for (Int32 i = 0; i < _data.Length; i++)
            {
                _data[i] = resSplit[i + 1].Trim();
            }

            return SUCCESS;
        }




        public static Int32 ExecuteCommandBlocking(string _cmd, string _cmdStatus, object _sender, SendFunc _SendFunc, FinalFunc _FinalFunc)
        {
            string[] data;
            return ExecuteCommandBlocking(_cmd, _cmdStatus, out data, _sender, _SendFunc, _FinalFunc);
        }

        public static Int32 ExecuteCommandBlocking(string _cmd, string _cmdStatus, out string[] _data, object _sender,SendFunc _SendFunc, FinalFunc _FinalFunc)
        {
            Int32 rt;
            string[] d;

            rt = ExecuteCommand(_cmd, out _data, _sender, _SendFunc, _FinalFunc);
            if(rt != SUCCESS)
                return ERROR;

            Thread.Sleep(DELAY_SHORT);

            while (true)
            {
                rt = ExecuteCommand(_cmdStatus, out d, _sender, _SendFunc, _FinalFunc);
                if(rt != SUCCESS)
                    return ERROR;

                if (d[0].Split()[0] == "IDLE")
                    break;

                Application.DoEvents();
                Thread.Sleep(POLL_TIME_MS);
            }

            return SUCCESS;
        }

    }
}
