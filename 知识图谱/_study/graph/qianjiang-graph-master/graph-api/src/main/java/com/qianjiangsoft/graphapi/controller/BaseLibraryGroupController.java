package com.qianjiangsoft.graphapi.controller;

import com.qianjiangsoft.graphcore.dto.BaseLibraryGroupDTO;
import com.qianjiangsoft.graphcore.param.BaseLibraryGroupParam;
import com.qianjiangsoft.graphapi.service.BaseLibraryGroupService;
import io.swagger.annotations.Api;
import io.swagger.annotations.ApiOperation;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import javax.validation.Valid;
import java.util.List;

/**
 * 基础-分组信息Controller
 *
 * @author YoCiyy
 * @date 2021-03-25
 */
@Api(tags = "分组信息")
@Slf4j
@RequestMapping("/base/library/group")
@RestController
@RequiredArgsConstructor
public class BaseLibraryGroupController {

    @Autowired
    BaseLibraryGroupService baseLibraryGroupService;

    /**
     * 查询基础-分组信息列表
     */
    @GetMapping("/list")
    @ApiOperation(value = "查询基础-根据分组信息列表", response = BaseLibraryGroupDTO.class)
    public List<BaseLibraryGroupDTO> list() {
        return baseLibraryGroupService.selectBaseLibraryGroupList();
    }

    /**
     * 获取基础-分组详细信息
     */
    @GetMapping(value = "/{id}")
    public BaseLibraryGroupDTO getInfo(@PathVariable("id") String id) {
        return baseLibraryGroupService.selectBaseLibraryGroupById(id);
    }

    /**
     * 同一分组名称唯一性校验
     */
    @PostMapping("/checkUnique")
    @ApiOperation("同一分组名称唯一性校验")
    public void checkGroupName(@Valid @RequestBody BaseLibraryGroupParam libraryGroupParam) {
        baseLibraryGroupService.checkGroupNameExists(libraryGroupParam);
    }

    /**
     * 新增基础-分组
     */
    @PostMapping("/add")
    @ApiOperation("新增基础-分组信息")
    public void add(@RequestBody BaseLibraryGroupParam baseLibraryGroupParam) {
        baseLibraryGroupService.insertBaseTableGroup(baseLibraryGroupParam);
    }

    /**
     * 修改基础-分组信息
     */
    @PostMapping("/update")
    @ApiOperation("修改基础-分组信息")
    public void edit(@RequestBody BaseLibraryGroupParam baseLibraryGroupParam) {
        baseLibraryGroupService.updateBaseTableGroup(baseLibraryGroupParam);
    }

    /**
     * 修改基础-分组信息
     */
    @PostMapping("/drag")
    @ApiOperation("修改基础-分组信息 拖拽功能")
    public void drag(@RequestParam("id") String id, @RequestParam("parentId") String parentId) {
        baseLibraryGroupService.dragBaseLibraryGroup(id, parentId);
    }

    /**
     * 删除基础-库信息
     */
    @DeleteMapping("/delete/{id}")
    public void remove(@PathVariable String id) {
        baseLibraryGroupService.deleteBaseLibraryGroupById(id);
    }

}
