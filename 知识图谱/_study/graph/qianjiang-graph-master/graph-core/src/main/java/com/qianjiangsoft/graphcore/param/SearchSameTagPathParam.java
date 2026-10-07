package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.model.Tag;
import com.qianjiangsoft.graphcore.model.Vertex;
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
public class SearchSameTagPathParam {

    private Tag tag;
    private Long maxStep;

    private List<Vertex> vertexList;
}
