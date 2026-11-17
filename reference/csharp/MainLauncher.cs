private Int32 Com(object _sender, ref string _req, ref string _res)
{
    Int32 rt;
    rt = __clientUi.SendBlocking(ref _req, ref _res);
    if (rt < 0)
    {
        _res = "Error: " + this.GetType().Name + "." + System.Reflection.MethodBase.GetCurrentMethod().Name + " " + _res;
        return -1;
    }

    return 1;
}
private void ComFinal(object _sender, ref string _req, ref string _res)
{
    if (_res.Contains("ERROR"))
    {
        AddErrorMessage("req: " + _req);
        AddErrorMessage("res: " + _res);
        return;
    }

    AddRequestMessage("req: " + _req);
    AddResponseMessage("res: " + _res);
}