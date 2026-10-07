package com.qianjiangsoft.graphapi.dto;

import cn.afterturn.easypoi.excel.annotation.Excel;
import cn.afterturn.easypoi.excel.annotation.ExcelIgnore;
import cn.afterturn.easypoi.excel.annotation.ExcelTarget;
import com.qianjiangsoft.graphcore.model.EdgeType;
import com.qianjiangsoft.graphcore.model.Vertex;
import lombok.Data;

import java.util.Date;
import java.util.Map;

/**
 * @author ulmarr8861
 * @date 2022/6/28
 */
@Data
public class EdgeReturnDTO {

    @Excel(name = "边")
    private String id;

    @Excel(name = "起始点")
    private String source;

    @Excel(name = "目标点")
    private String destination;

    @Excel(name = "边类型")
    private String type;

    @Excel(name = "强度属性")
    private Double strength;

    @Excel(name = "时间属性")
    private Date time;

    @ExcelIgnore
    private Map<String, Object> propertyList;
}
