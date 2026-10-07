package com.qianjiangsoft.graphapi.param;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * @author marshallwu
 * @date 2022/4/28 18:02
 */
@Data
@AllArgsConstructor
@NoArgsConstructor
public class SearchSameTagRelationParam {

    private String tagName;
    private Long maxStep;
    private List<String> vertexIdList;

    private String taskName;
}
