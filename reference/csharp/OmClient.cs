using System;
using System.Collections.Generic;
using System.Linq;
using System.Net.Sockets;
using System.Text;
using System.Threading.Tasks;

namespace RobotServerBL22HT
{
    public class OmClient
    {
        //-----------------------------------------------------------
        public const Int32 SUCCESS = 1;
        public const Int32 ERROR = -1;
        //-----------------------------------------------------------


        #region Variables        
        //-----------------------------------------------------------
        private TcpClient __client;
        private StreamReader __reader;
        private StreamWriter __writer;
        //-----------------------------------------------------------
        #endregion



        #region Properties
        //-----------------------------------------------------------

        //-----------------------------------------------------------
        #endregion



        //===========================================================
        public OmClient(string _ipAddress, Int32 _port)
        {
            __client = new TcpClient(_ipAddress, _port);
        }



        #region Private Methods
        //-----------------------------------------------------------
        //-----------------------------------------------------------
        #endregion



        #region Public Methods
        //-----------------------------------------------------------
        public Int32 Connect()
        {
            __reader = new StreamReader(__client.GetStream(), Encoding.ASCII);
            __writer = new StreamWriter(__client.GetStream(), Encoding.ASCII);

            if (__reader == null || __writer == null)
                return ERROR;

            return SUCCESS;
        }
        public void Disconnect()
        {
            __reader.Close();
            __writer.Close();
            __client.Close();
        }
        //-----------------------------------------------------------
        public Int32 SendBlockingRaw(ref string _req, ref string _res)
        {
            const string TERMINATOR = "\r\n";
            Int32 n;
            char[] buffer = new char[8192];
            StringBuilder sb = new StringBuilder(10000);

            try
            {
                __writer.WriteLine(_req);
                __writer.Flush();

                sb.Append((char)__reader.Read());
                
                while (true)
                {
                    n = __reader.Read(buffer, 0, buffer.Length);
                    if (n == 0)
                        return ERROR;
                    
                    sb.Append(buffer, 0, n);

                    if (sb.Length >= TERMINATOR.Length && sb.ToString(sb.Length - TERMINATOR.Length, TERMINATOR.Length) == TERMINATOR)
                        break;
                }

                _res = sb.ToString();
                _res = _res.Substring(0, _res.Length - TERMINATOR.Length);
            }
            catch (Exception ex)
            {
                Console.WriteLine(ex.Message);
                return ERROR;
            }
            return SUCCESS;
        }
        //-----------------------------------------------------------
        #endregion
    }
}
