package com.qianjiangsoft.graphcore;

import cn.afterturn.easypoi.excel.ExcelExportUtil;
import cn.afterturn.easypoi.excel.entity.ExportParams;
import cn.afterturn.easypoi.excel.entity.params.ExcelExportEntity;
import com.qianjiangsoft.graphcore.constants.SortingDirection;
import com.qianjiangsoft.graphcore.util.CommonServletUtil;
import lombok.extern.slf4j.Slf4j;
import org.apache.poi.ss.usermodel.Workbook;
import org.junit.Test;
import org.springframework.boot.test.context.SpringBootTest;

import javax.servlet.ServletOutputStream;
import javax.servlet.http.HttpServletResponse;
import java.io.PrintWriter;
import java.net.URLEncoder;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

@SpringBootTest(classes = EnumTests.class)
@Slf4j
public class EnumTests {

    @Test
    public void testSwitch(){
        SortingDirection conditionType = SortingDirection.asc;

        switch (conditionType) {
            case desc:
                log.info("desc");
                break;
            case asc:
                log.info("asc");
                break;
        }
    }
    @Test
    public void testExport() {
        HttpServletResponse response = CommonServletUtil.getResponse();
        exportDeliveryExpense(response);


    }
    public void exportDeliveryExpense(HttpServletResponse response) {
        // 查询需要导出的数据
        List<Map<String, Object>> dtoLst = new ArrayList<>();
        Map<String, String> map1 = new HashMap<>();
        map1.put("name", "楚阳");
        map1.put("address", "傲世九重天");
        map1.put("specialty", "九劫剑");
        Map<String, String> map2 = new HashMap<>();
        map2.put("name", "叶凡");
        map2.put("address", "遮天");
        map2.put("specialty", "荒古圣体");
        // 文件名称
        String fileName = "人员列表.xls";
        // 导出列名和字段code
        String nameStr = "姓名,书名,特长";
        String codeStr = "name,address,specialty";
        List<ExcelExportEntity> entity = setExcelExportEntity(nameStr.split(","), codeStr.split(","));
        // 文件导出
        export(response, entity, dtoLst, fileName);
    }

    public List<ExcelExportEntity> setExcelExportEntity(String[] nameArr, String[] codeArr) {
        List<ExcelExportEntity> entity = new ArrayList<>();
        for(int i=0; i < nameArr.length; i++) {
            entity.add(new ExcelExportEntity(nameArr[i], codeArr[i]));
        }
        return entity;
    }


    public void export(HttpServletResponse response, List<ExcelExportEntity> entity, List<Map<String, Object>> dtoLst, String fileName) {
        try {
            // easyPoi操作excel
            Workbook workbook = ExcelExportUtil.exportExcel(new ExportParams(), entity, dtoLst);
            // 设置响应头
            response.setHeader("content-Type", "application/vnd.ms-excel;charset=UTF-8");
            response.setHeader("Content-Disposition", "attachment;filename=" + URLEncoder.encode(fileName, "utf-8"));
            // 写入到流中
            ServletOutputStream outputStream = response.getOutputStream();
            workbook.write(outputStream);
            workbook.close();
            outputStream.close();
        } catch (Exception e) {
            e.printStackTrace();
            try {
                response.setHeader("Content-type", "text/html;charset=UTF-8");
                response.setCharacterEncoding("UTF-8");
                PrintWriter pw = response.getWriter();
                pw.write("导出文件异常！");
            } catch (Exception e1) {
                e.printStackTrace();
            }
        }
    }



}
