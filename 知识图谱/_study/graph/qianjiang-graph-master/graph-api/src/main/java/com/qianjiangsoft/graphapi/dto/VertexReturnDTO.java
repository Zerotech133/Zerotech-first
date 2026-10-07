package com.qianjiangsoft.graphapi.dto;

import com.qianjiangsoft.graphcore.model.Tag;
import lombok.Data;

import java.util.Map;

/**
 * @author ulmarr8861
 * @date 2022/4/12
 */
@Data
public class VertexReturnDTO {
    String id;

    String name;

    Map<String, Object> propertyList;

    Tag tag;
}
